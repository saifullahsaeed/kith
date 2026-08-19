"""Talking to him."""

from __future__ import annotations

import contextvars
import json
import threading
import time
from collections import Counter

from flask import Response, jsonify, request

from kith import tools
from kith.api.blueprint import api
from kith.config import (
    default_config,
    merge_overrides,
    ollama_host,
)
from kith.infra import permissions
from kith.infra.db import repositories as repo
from kith.kernel import clock, live_turns, session_context, stopping
from kith.llm import ledger
from kith.llm.budget import SEED_CHARS_PER_TOKEN, message_chars
from kith.schemas import (
    AnswerSchema,
    ChatRequestSchema,
)
from kith.services import conversations, history, questions, steering
from kith.services.activity import describe_call, short_args
from kith.services.agent_loop import stream_agent
from kith.services.turn.prompt import (
    _build_messages,
    _conversation_chars,
)
from kith.services.turn.prompt import (
    as_sent as prompt_as_sent,
)
from kith.settings import AGENT_DB_PATH

#: Turns in flight, by conversation, so Stop has something to set.
#:
#: A turn used to be stopped by hanging up: the client aborted the fetch, the response generator
#: raised GeneratorExit, and the turn died with it. That is gone now the turn runs on its own —
#: which is the point, since it is also what killed a turn when you merely switched conversations
#: — so stopping has to be said out loud rather than inferred from a closed socket. The two
#: things were never the same intent; they only looked the same over HTTP.
#:
#: One slot per conversation, but the switch belongs to a *turn*, and a conversation outlives
#: the turn running in it. That gap is not theoretical now nothing hangs up: send a message,
#: switch away, come back and send another, and two turns share this dict. Reached through the
#: four helpers below rather than directly, because every one of them turns on the identity of
#: the event rather than on the key — see `_disarm` for the bug that reads as "Stop does
#: nothing".
#: The registry moved to `kernel/stopping.py`, and this name is kept as the alias the tests and
#: this module already reach for. It moved because a *tool* has to read it now: a sub-agent runs
#: a whole second loop inside one tool call and the parent loop emits nothing while it does, so
#: the between-events check below cannot fire and Stop did nothing for the length of an errand.
#: A tool cannot import a route, so the switch went to where `session_context` and `live_turns`
#: already live. What stays here is the decision to stop, which is the part that needs services.
_RUNNING = stopping._RUNNING
_arm = stopping.arm
_disarm = stopping.disarm
_current = stopping.current


def _stop(conversation_id: str) -> bool:
    """Ask the turn running in a conversation to stop. False if there was none.

    Order matters here, and it used to be the other way round.

    A turn parked on a question is not reading the switch — it is inside a tool call, waiting on
    an event — so the waits have to be released or Stop does nothing for up to fifteen minutes.
    But releasing *first* hands the turn back its next round before it has been told to stop:
    `ask` returns the moment the event is set, the loop appends the result, `_turn` yields it and
    only then reads the flag. Lose that race and the flag is still unset, so the turn carries
    on — one more model call, on the full conversation, after the person pressed Stop. Which is
    what "I stopped it and it started again" is.

    So the flag goes on first and the waits are released after. The window closes rather than
    merely being small: by the time anything the release woke can reach a check, the flag it
    reads is already set.
    """
    was_running = stopping.stop(conversation_id)
    # Now nothing that wakes up can get past its next check, so it is safe to wake it.
    questions.release(conversation_id)
    permissions.release_waiting()
    return was_running


@api.get("/chat/<conversation_id>/question")
@api.doc(
    summary="The question this conversation is waiting on, if any",
    description="Read on attaching to a turn already in flight, whose question card scrolled past before you were watching.",
)
def open_question(conversation_id: str):
    return jsonify(questions.open_question(conversation_id) or {})


@api.post("/questions/<question_id>/answer")
@api.doc(
    summary="Answer a question he is waiting on",
    description="Releases the turn. One entry per question: chosen labels, free text, or skipped.",
)
@api.input(AnswerSchema, arg_name="payload")
def answer_question(question_id: str, payload: dict):
    return jsonify({"answered": questions.answer(question_id, payload.get("answers") or [])})


@api.get("/chat/<conversation_id>/working-on")
@api.doc(
    summary="The task this conversation is working, with its checklist",
    description="`{}` when it is not on one. Polled, so the checklist ticks as he goes.",
)
def working_on(conversation_id: str):
    return jsonify(repo.tasks.working_in(AGENT_DB_PATH, conversation_id) or {})


@api.post("/chat/<conversation_id>/fold")
@api.doc(
    summary="Fold this conversation's older turns into a brief, now",
    description="What a turn does for itself when the window gets tight, asked for on purpose.",
)
def fold_now(conversation_id: str):
    """Summarise the older half of a conversation on demand.

    The fold already exists; it just could not be *asked* for. A turn folds itself when the
    window gets tight, which means the one moment you might want it — before sending something
    long into a conversation you know is bloated — is the one moment it will not happen, and
    the fold instead lands in the middle of the turn you were waiting on.

    Reports what it removed, in the same shape the automatic one streams, so the interface can
    say the same sentence either way.
    """
    messages = conversations.full_messages(conversation_id)
    before = _conversation_chars(messages)
    # The same categorisation the meter shows, denominated in characters — `chars_per_token=1`
    # makes `_tokens` the identity, so every line is a raw character count. Characters are the
    # one unit both sides of a fold can be compared in without knowing what the provider charges
    # per token; the conversion happens once, at the end, in `_reading_after_fold`.
    #
    # Taken before the fold rather than after, because `compact` is free to hand back the list it
    # was given, and a "before" measured from the same object as the "after" would report that
    # nothing changed.
    was = ledger.take(messages, chars_per_token=1.0)
    folded, brief = history.fold(
        messages, default_config(), conversation_id, ollama_host(), _tool_block_chars(), force=True
    )
    if brief is None:
        # Two different answers, and telling them apart is the point. `fold` declines either
        # because there is barely anything here, or because everything but the recent turns is
        # already a brief — and reporting both as "not enough here to be worth folding" on a
        # conversation of six hundred thousand tokens reads as the command being broken. It was
        # not; it had nothing left to do and said so in the wrong words.
        already = bool(conversations.latest_summary(conversation_id).get("text"))
        return jsonify(
            {
                "folded": False,
                "note": (
                    "Already folded — only the recent turns are left, and those are the ones "
                    "worth keeping whole."
                    if already
                    else "There is not enough here yet to be worth folding."
                ),
            }
        )
    # Persisted, which is the entire difference between a fold and an expensive no-op.
    #
    # `history.fold` is pure but for the summariser: it hands back the folded list and the brief
    # it made, and says in its own docstring that the caller persists it. The turn path does
    # (`_build_messages`). This one did not — so `/fold` paid for a summarisation call, reported
    # how many characters it had removed, and dropped the brief on the floor. The next turn read
    # the untouched transcript, found itself under the fold threshold, and replayed the whole
    # conversation: the meter fell to 25k and came straight back to 612k on the next message,
    # which is precisely what "no effect at all" looks like from outside.
    #
    # Saving it is enough to make it stick. `compact` skips its "short and never folded" exit the
    # moment a brief exists, and reuses the stored one for as long as the turns since it fit the
    # budget — so the next turn replays the brief plus the recent tail rather than everything.
    conversations.record_summary(conversation_id, brief["through"], brief["text"])
    after = _conversation_chars(folded)
    return jsonify(
        {
            "folded": True,
            "fromChars": before,
            "toChars": after,
            "reading": _reading_after_fold(conversation_id, was, ledger.take(folded, chars_per_token=1.0)),
        }
    )


def _reading_after_fold(conversation_id: str, was: ledger.Ledger, now: ledger.Ledger) -> dict | None:
    """How full the window is now that the fold has happened.

    A reading has only ever existed as something a turn took on its way past: the loop measures
    the request it is about to send, and the last such measurement is what the meter shows. A
    fold is not a turn. It rewrites the stored history and answers, and nothing measures anything
    — so the meter went on showing a conversation that no longer exists, and the one command
    whose entire purpose is to make that number smaller left it exactly where it was.

    This does not rebuild the request to find out. Rebuilding means the persona, the live block,
    the tool schemas as they will be narrowed next turn, and the token ratio the provider's own
    billing calibrated — four things to get right in order to re-derive a number that is mostly
    unchanged. A fold moves the stored conversation and nothing else, so: start from the last
    real reading, subtract what actually left, in the ratio that reading was costed with, and
    carry the untouched categories across rather than re-estimating them.

    `None` when the conversation has never had a turn, which is the only honest answer — there
    is no measurement to adjust, and a first reading is the next turn's to take.
    """
    taken = conversations.latest_reading(conversation_id)
    reading = taken.get("context") or taken.get("baseline") or {}
    if not reading.get("lines"):
        return None
    # The seed only for readings recorded before `charsPerToken` was sent. A fold typically
    # removes hundreds of thousands of characters, so the ratio it is divided by is the whole
    # difference between "the meter moved by the right amount" and "the meter moved".
    ratio = float(reading.get("charsPerToken") or 0) or SEED_CHARS_PER_TOKEN
    previous = {str(line.get("key") or ""): int(line.get("tokens") or 0) for line in reading["lines"]}
    window = int(reading.get("window") or 0)

    lines = []
    for line in now.lines:
        # Signed on purpose: the brief the fold wrote is itself context, and in a category that
        # may have held nothing before. A fold that summarises 400k characters into 3k has to
        # show the 3k arriving as well as the 400k leaving, or the meter reads low by the size
        # of the summary and the next turn appears to grow for no reason.
        removed = (was.of(line.key) - now.of(line.key)) / ratio
        tokens = max(0, round(previous.get(line.key, 0) - removed))
        if tokens > 0:
            lines.append(
                {
                    "key": line.key,
                    "label": line.label,
                    "tokens": tokens,
                    # Zero when the window is unknown, exactly as `Line.share_of` does it: the
                    # absolute figures are real on a local model, only the percentages are not.
                    "share": round(tokens / window, 4) if window > 0 else 0.0,
                }
            )

    used = sum(line["tokens"] for line in lines)
    return {
        "window": window,
        "used": used,
        "free": max(0, window - used) if window > 0 else 0,
        "share": round(used / window, 4) if window > 0 else 0.0,
        "charsPerToken": ratio,
        "lines": lines,
    }


@api.get("/chat/<conversation_id>/context")
@api.doc(
    summary="What is in this conversation's window, itemised",
    description="The meter's own categories, subdivided into the calls that filled them.",
)
def context_detail(conversation_id: str):
    """The reading the meter is showing, plus which calls actually filled it.

    **Its own endpoint, rather than more fields on the streamed `context` event.** That event is
    emitted every turn and persisted into the transcript — 167 of them on one real conversation
    here — so hanging two and a half thousand items off each would grow every stored turn forever
    to serve a screen that is open for a few seconds a month, and would charge that cost to
    everyone who never opens it. A reading is streamed; a breakdown is asked for.

    **The totals are the stored reading's, untouched.** Not recomputed: a fresh `take` here would
    need the schemas as they will be narrowed next turn, the live block as it will be written, and
    the ratio the provider's own billing calibrated — four things to get right in order to
    re-derive a number the meter is already showing correctly. Getting any of them slightly wrong
    produces a detail screen that quietly disagrees with the rail beside it, and there is no way
    for the person to tell which of the two is lying.

    **The items are the conversation, not the window, and the response keeps them apart.** A
    reading measures the request a turn actually sent, which is the conversation *after* the fold
    — old turns replaced by a brief, their tool results gone with them. `full_messages` is the
    transcript: everything that ever happened. On one real conversation here those are 556,392
    and 2,361,997 tokens, so the window is a quarter of the history.

    The first version of this treated the items as a breakdown *of* the reading and printed a
    repeat figure worth "121% of the window", which is how the confusion announced itself.
    Reproducing the folded list instead is not an option: folding is a summarisation call, and a
    screen you open in order to look at something must never spend money to draw itself.

    Both figures are worth having. `lines`/`used` are the window, and agree with the meter. The
    items and `wasted` are the conversation, totalled separately in `itemsTotal` so nothing has to
    express one as a share of the other. The items are still the actionable half — a file read
    fifty-one times is a habit that will refill the window whether or not those particular copies
    survived the last fold.
    """
    taken = conversations.latest_reading(conversation_id)
    reading = taken.get("context") or taken.get("baseline") or {}
    # The ratio the reading was costed with, so the items are denominated in its units rather than
    # in a guess. The seed only for readings recorded before the field existed.
    ratio = float(reading.get("charsPerToken") or 0) or SEED_CHARS_PER_TOKEN
    items = ledger.itemise(conversations.full_messages(conversation_id), chars_per_token=ratio)

    return jsonify(
        {
            # False on a conversation that has never had a turn. The screen needs to say "nothing
            # has measured this yet" rather than draw an empty window at 0%, which reads as an
            # answer and is not one.
            "reading": bool(reading.get("lines")),
            "window": int(reading.get("window") or 0),
            "used": int(reading.get("used") or 0),
            "free": int(reading.get("free") or 0),
            "share": float(reading.get("share") or 0.0),
            "folded": bool(taken.get("folded")),
            "lines": reading.get("lines") or [],
            # ── everything below is the conversation, not the window. See above. ──
            "items": ledger.items_as_wire(items),
            # Led with, because it is the only figure here that is a decision rather than a fact.
            "wasted": sum(item.wasted for item in items),
            # The items' own denominator. Without it the screen has nothing to express `wasted` as
            # a share of except `used`, which is a different measurement and gave "121%".
            "itemsTotal": sum(item.tokens for item in items),
            # ── the prompt itself, message by message ──
            "sent": _as_sent(conversation_id, ratio),
            # What the provider actually billed for the last round. Everything else on this
            # screen is `message_chars` over a ratio; these came back from the provider.
            "lastRound": _last_round(conversation_id),
        }
    )


#: How much of a message the list carries. Enough to recognise a row; not enough to make the
#: response large. The whole text is one request away, for the one you click.
_PREVIEW_CHARS = 240


def _sent_messages(conversation_id: str) -> tuple[list[dict], bool]:
    """The list a turn would send, and whether a fold is owed before it does."""
    messages = conversations.full_messages(conversation_id)
    return prompt_as_sent(messages, default_config(), conversation_id, tool_chars=_tool_block_chars())


def _previous_messages(conversation_id: str) -> list[dict] | None:
    """The prompt the last turn sent, rebuilt.

    Nothing records the literal list a turn sent, and nothing needs to: the prompt is a function
    of the transcript, so the last turn's prompt is that same function over the transcript as it
    stood when that turn began — everything up to and including the user message that started it.

    `None` on a conversation whose first turn has not happened yet. Saying "+100% since last turn"
    against a turn that never ran would be inventing the comparison rather than making one.
    """
    messages = conversations.full_messages(conversation_id)
    starts = [i for i, message in enumerate(messages) if message.get("role") == "user"]
    if not starts:
        return None
    # The turn in progress (or the last one) began at the final user message; the prompt it was
    # handed ended there. Everything after it is what that turn itself produced.
    cut = starts[-1]
    if cut == 0:
        return None  # the very first turn — there is no prompt before it
    return prompt_as_sent(messages[:cut], default_config(), conversation_id, tool_chars=_tool_block_chars())[
        0
    ]


def _key(message: dict) -> tuple:
    """What makes two messages the same message across two builds of the prompt.

    Content rather than position: a fold changes where a message sits without changing what it
    is, and diffing by index would report the entire tail as replaced every time one happened.
    """
    content = message.get("content")
    return (
        str(message.get("role") or ""),
        str(message.get("tool_name") or ""),
        content if isinstance(content, str) else json.dumps(content, sort_keys=True),
        json.dumps(message.get("tool_calls"), sort_keys=True) if message.get("tool_calls") else "",
    )


def _as_sent(conversation_id: str, ratio: float) -> dict:
    """The prompt, message by message, costed the way the ledger costs it.

    Same `message_chars` and same ratio as the categories above, so a row's tokens and the
    category it lands in are the same measurement rather than two that nearly agree.
    """
    messages, fold_pending = _sent_messages(conversation_id)
    previous = _previous_messages(conversation_id)

    # What the last turn's prompt held, counted so each message here can be told apart from one
    # that merely looks like it. A `Counter` rather than a set: the same tool result really can
    # appear twice, and two copies last turn against two copies now is "kept, kept" — treating it
    # as a set would call the second one new for the rest of the conversation's life.
    was = Counter(_key(message) for message in previous or [])
    seen: Counter = Counter()

    listed = []
    for index, message in enumerate(messages, start=1):
        chars = message_chars(message)
        key = _key(message)
        live = bool(message.get("_live"))
        if live:
            # Neither kept nor added. It is rewritten every single turn — which is the honest
            # answer, and the one worth teaching: it is why the tail of a prompt is never cached.
            change = "rewritten"
        elif previous is None:
            change = "added"
        else:
            seen[key] += 1
            change = "kept" if seen[key] <= was[key] else "added"
        listed.append(
            {
                "index": index,
                "role": str(message.get("role") or ""),
                # "tool" is a role, not an answer — which tool ran is what makes the row
                # identifiable in a list of forty of them.
                "tool": str(message.get("tool_name") or ""),
                "chars": chars,
                "tokens": int(chars / ratio) if ratio > 0 else 0,
                "preview": _preview(message),
                "change": change,
                "live": live,
                # The calls this message carries, with the arguments they were made with. A tool
                # call is a real message in the prompt and the command inside it is usually the
                # only part that says what it was — the screen folds a call into its result, and
                # a fold that dropped the arguments would be hiding something that is sent.
                "calls": _calls_of(message),
            }
        )

    # What the last turn carried and this one will not: what a fold or a trim removed. Reported
    # separately because it is not in the list — it is the part of the answer that is missing
    # from it, and a screen that only ever grows explains half of context management.
    remaining = was - seen
    dropped = []
    for message in previous or []:
        key = _key(message)
        if remaining[key] and not message.get("_live"):
            remaining[key] -= 1
            chars = message_chars(message)
            dropped.append(
                {
                    "role": str(message.get("role") or ""),
                    "tool": str(message.get("tool_name") or ""),
                    "tokens": int(chars / ratio) if ratio > 0 else 0,
                    "preview": _preview(message),
                }
            )

    by_role: dict[str, dict] = {}
    for row in listed:
        seen = by_role.setdefault(row["role"], {"role": row["role"], "tokens": 0, "count": 0})
        seen["tokens"] += row["tokens"]
        seen["count"] += 1
    total = sum(row["tokens"] for row in listed)
    for seen in by_role.values():
        seen["share"] = round(seen["tokens"] / total, 4) if total else 0.0

    def costed(items) -> int:
        return sum(int(message_chars(m) / ratio) if ratio > 0 else 0 for m in items)

    return {
        # Said out loud rather than papered over: the preview never pays for a fold, so on a
        # conversation that is due one this is the prompt that would go if it did not.
        "foldPending": fold_pending,
        "tokens": total,
        "messages": listed,
        # Largest first — "why is this prompt so big" is almost always one role.
        "byRole": sorted(by_role.values(), key=lambda row: -row["tokens"]),
        # ── against the prompt the last turn sent ──
        "hasPrevious": previous is not None,
        "previousTokens": costed(previous or []),
        # Carved out of both sides so the arithmetic closes. The live block is rewritten rather
        # than added, so it is neither growth nor carry-over; leaving it in either total makes
        # "before + added = after" fail by a few thousand tokens for a reason nobody can find.
        "previousLiveTokens": costed([m for m in (previous or []) if m.get("_live")]),
        "addedTokens": sum(row["tokens"] for row in listed if row["change"] == "added"),
        "dropped": dropped,
        "droppedTokens": sum(row["tokens"] for row in dropped),
    }


def _calls_of(message: dict) -> list[dict]:
    """Each tool call on this message, with its arguments rendered to one readable line.

    Structured rather than folded into the preview text, because two callers read it and both
    want it exact: the screen prints the command, and the pairing that folds a call into its
    result matches on `name`. Matching instead on a preview that happens to read "calls grep" is
    a string comparison against prose.
    """
    out = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (ValueError, TypeError):
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        # One argument is the command, and printing `{"command": "pytest -q"}` around it buys
        # nothing. More than one and the keys are what tell them apart.
        if len(arguments) == 1:
            only = next(iter(arguments.values()))
            args = only if isinstance(only, str) else json.dumps(only, ensure_ascii=False)
        else:
            args = json.dumps(arguments, ensure_ascii=False)
        out.append(
            {
                "name": str(function.get("name") or ""),
                "args": " ".join(str(args).split())[:_PREVIEW_CHARS],
            }
        )
    return out


def _preview(message: dict) -> str:
    """A line you can scan, which is not the same job as the message body.

    A tool result's content is `json.dumps(result)`, and a result that was itself a JSON string
    comes out doubly encoded — the first version of this listed `"\\"ok\\": true, \\"result\\":
    {\\"name\\": ...` for every tool row, which is unreadable and is most of the list. Unwrapped
    here and only here: the detail pane still shows the literal text the provider receives, since
    that is the entire point of the screen.
    """
    content = message.get("content")
    if isinstance(content, list):
        # An attachment-carrying message: its parts, not a JSON dump of the envelope.
        text = " ".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    else:
        text = _unwrapped(str(content or "")) if message.get("role") == "tool" else str(content or "")
    if not text.strip() and message.get("tool_calls"):
        names = [str((call.get("function") or {}).get("name") or "") for call in message["tool_calls"]]
        text = f"calls {', '.join(n for n in names if n)}"
    return " ".join(text.split())[:_PREVIEW_CHARS]


def _unwrapped(text: str) -> str:
    """The readable part of a JSON-encoded tool result, or the text unchanged.

    Peels at most twice — a result that is a JSON string inside a JSON envelope is the shape the
    loop actually produces — and gives up quietly on anything that is not JSON, which is most
    shell output.
    """
    for _ in range(2):
        try:
            value = json.loads(text)
        except (ValueError, TypeError):
            return text
        if isinstance(value, dict):
            # `{"ok": true, "result": ...}` — the result is the part worth showing.
            inner = value.get("result", value)
            text = inner if isinstance(inner, str) else json.dumps(inner)
        elif isinstance(value, str):
            text = value
        else:
            return json.dumps(value)
    return text


def _last_round(conversation_id: str) -> dict | None:
    """What the provider billed for the most recent round, or None if there has not been one.

    None rather than zeroes: a cost of $0.00 and a model of "" read as facts, and they are the
    absence of one.
    """
    found: dict = {}
    for entry in conversations.read(conversation_id):
        if entry.get("type") == "stats":
            found = entry.get("stats") or {}
    if not found:
        return None
    return {
        "model": str(found.get("model") or ""),
        "provider": str(found.get("provider") or ""),
        "promptTokens": int(found.get("promptTokens") or 0),
        "responseTokens": int(found.get("responseTokens") or 0),
        "cachedTokens": int(found.get("cachedTokens") or 0),
        "cacheWriteTokens": int(found.get("cacheWriteTokens") or 0),
        "costUsd": float(found.get("costUsd") or 0.0),
    }


@api.get("/chat/<conversation_id>/context/message/<int:index>")
@api.doc(
    summary="One message of the prompt, whole",
    description="The text behind a row in the context screen's list.",
)
def context_message(conversation_id: str, index: int):
    """One message in full, fetched when someone clicks the row.

    Its own request because the list must stay small. One real conversation here is 2.36M tokens
    of transcript; carrying every message's text in order to draw a list of previews would be a
    several-megabyte response for a screen that shows one at a time.
    """
    messages, _ = _sent_messages(conversation_id)
    if not 1 <= index <= len(messages):
        return jsonify({"index": index, "role": "", "tool": "", "text": ""})
    message = messages[index - 1]
    content = message.get("content")
    if isinstance(content, list):
        text = "\n\n".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    else:
        text = str(content or "")
    if not text.strip() and message.get("tool_calls"):
        text = json.dumps(message["tool_calls"], indent=2)
    return jsonify(
        {
            "index": index,
            "role": str(message.get("role") or ""),
            "tool": str(message.get("tool_name") or ""),
            "text": text,
        }
    )


@api.post("/chat/<conversation_id>/stop")
@api.doc(
    summary="Stop the turn running in a conversation",
    description="Asks it to stop after the event it is on. Returns whether there was one.",
)
def stop_turn(conversation_id: str):
    return jsonify({"stopping": _stop(conversation_id)})


@api.post("/chat/<conversation_id>/steer")
@api.doc(
    summary="Say something to the turn already running",
    description=(
        "Delivers text into a turn in progress, at its next round boundary. Nothing already "
        "worked out is lost — this is the alternative to Stop, which throws the run away. "
        "Returns {steering: false} when no turn is running, and the caller should send the "
        "text as an ordinary message instead."
    ),
)
def steer_turn(conversation_id: str):
    """Change course without killing the run.

    Recorded to the transcript here rather than by the loop, so the conversation reads in the
    order it happened: what was said, then the round that acted on it. The loop only appends it
    to the list it is sending — it has no business writing history, and a second writer would
    be a second answer to "what did they actually say".
    """
    body = request.get_json(silent=True) or {}
    said = str(body.get("message") or body.get("content") or "").strip()
    if not said:
        return jsonify({"error": "nothing to say"}), 400
    if live_turns.current(conversation_id) is None:
        # Not an error. The window asks, finds nothing running, and posts it as a new message —
        # which is the same thing the person meant, one round later.
        return jsonify({"steering": False, "reason": "no turn is running"})
    conversations.record(AGENT_DB_PATH, conversation_id, "user", said)
    steering.steer(conversation_id, said)
    return jsonify({"steering": True, "waiting": steering.waiting(conversation_id)})


@api.get("/chat/<conversation_id>/attach")
@api.doc(
    summary="Watch the turn already running in a conversation",
    description=(
        "The same stream the request that started it is reading: everything said so far, then "
        "the rest as it happens. 204 when nothing is running. Opening this does not start a "
        "turn and closing it does not stop one."
    ),
)
def attach_turn(conversation_id: str):
    """Join a turn in progress.

    A turn survives you leaving — it has run on its own thread since stopping became something
    said rather than inferred — but until now everything it said while you were away was
    unreadable, because the only reader was the request that began it. Switching conversations
    mid-answer therefore looked exactly like the turn being killed and restarted: silence, then
    the finished reply in one piece.

    Nothing here is a special case. It hands back `live_turns.watch`, which is the same call the
    original request makes; the difference between starting and attaching is only what the
    backlog contains when you arrive.
    """
    live = live_turns.current(conversation_id)
    if live is None:
        return Response(status=204)
    return Response(
        live_turns.watch(live),
        mimetype="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def begin_turn(conversation_id: str, config, gather, opening: str = ""):
    """Start a turn, supervised, and return the stream to watch. **The only way one begins.**

    There used to be two. A typed message came through here and got the whole apparatus — a
    live turn the window can follow, a stop switch, a steer queue, the session binding, the
    fold announcement, one place that closes the books however it ends. A turn Kith started
    himself — a reminder due, a background task finished — went through `continue_conversation`,
    which built the messages and drove `_turn` by hand and had none of it.

    Not a design. `continue_conversation` was written on 2026-08-14 to settle a layering
    complaint: `services/scheduler` was importing three *private* functions out of this module,
    the last upward edge in the tree. Exposing something public for it to call was right. What
    it exposed was a second copy of the turn half — written five days after `work()` had already
    been split off the request thread (2026-08-08) and the live turn had already stopped
    belonging to a connection (2026-08-09). The door it needed was open; it built another one.

    So the two paths cost you: an unattended turn could not be watched (the window has listened
    for `changes.publish("turn", ...)` since it shipped, and only `live_turns.begin` sends it —
    so the transcript grew on disk and you found out on reload), could not be stopped, and could
    not be steered — `/steer` answers "nothing is running", so typing at it started a *second*
    turn on the same conversation.

    `gather` is a callable, not a list, and that is the one piece of this that is not simply a
    move. Reading the transcript and building the prompt happen on the turn's own thread because
    building it can fold, and a fold is a model call: measured at a 1.57M-character backlog, a
    full round trip before the real request was sent. Handing this a finished list would drag
    that back onto whichever thread called — the request thread for a chat, the scheduler's one
    timer thread for a reminder.
    """
    # A turn is a loop, and the transcript keeps its shape: what he reasoned, what he said,
    # what he called and what came back, in the order it happened. Anything less and a resumed
    # conversation is a summary of itself.
    recorder = _Recorder(conversation_id)
    # Not a queue owned by this request any more — see `services/live_turns`. The turn's output
    # outlives the connection that asked for it, so leaving mid-answer and coming back attaches
    # to the same stream instead of finding a finished wall of text.
    live = live_turns.begin(conversation_id)

    # Held as a local for the life of this turn, not re-read from `_RUNNING` between events.
    # See `_arm`: the dict says which turn is current, and a turn asking that question about
    # itself gets the wrong answer the moment a second one starts in the same conversation.
    stopping = _arm(conversation_id)

    def work():
        """Advance the turn to the end, whether or not anyone is still reading.

        This used to be the body of the response generator, and that is what made the browser
        load-bearing: a generator only advances when something pulls on it, and the thing
        pulling was Flask writing to the socket. Close the tab, switch conversations, drop the
        wifi, and the turn stopped mid-step — not because anything cancelled it, but because
        nothing was left asking for the next one. Work already done survived (the recorder
        writes as it goes); the rest simply never happened.

        Now the turn runs here, on its own, and the response below is only a reader. What a
        disconnect costs you is the live view, not the turn.
        """
        try:
            # Bound for the whole turn, so a tool that acts on a project records that this
            # session is the one working on it. Starting a project here and having nothing
            # know whose it was is how `conversations.project_id` stayed null from the day it
            # was added: read on every turn to pick the project memory, written by nobody.
            #
            # Entered *inside* the worker, not around it. The binding is a ContextVar, and a
            # thread does not inherit its parent's — a `with` in the request thread would leave
            # every tool call in here believing it belonged to no conversation, which is silent
            # rather than loud: files still get written, and nothing records whose turn wrote
            # them. See `copy_context` below for the other half of that.
            with session_context.working_in(conversation_id):
                # Reading the transcript and building the prompt happen *here*, not on the
                # request path, because building it can fold — and a fold is a summarisation
                # call to the model. On a long conversation it is a large one: measured on a
                # real transcript, a 1.57M-character backlog, about 390k tokens, a full
                # round-trip before the actual request was even sent.
                #
                # It also said nothing while it did it. The `compacting` event is emitted from
                # inside the turn, and the turn had not started — so the one thing that could
                # have explained the wait was structurally unable to fire. It reads as "he
                # takes ages before he answers", and every explanation you reach for first —
                # the reasoning effort, a slow provider — is wrong, because those come after.
                #
                folded: dict = {}
                messages = _build_messages(
                    gather(), config, conversation_id, folded, tool_chars=_tool_block_chars()
                )
                if folded.get("happened"):
                    # Say so, and say how much went. The context reading the meter shows is
                    # taken *after* this, so a conversation several times over its window reads
                    # as comfortable and the fold looks gratuitous — the one number a person
                    # checks is the one number that cannot show the problem.
                    live_turns.publish(
                        live,
                        json.dumps(
                            {
                                "type": "compacting",
                                "foldedFrom": folded["fromChars"],
                                "foldedTo": folded["toChars"],
                            }
                        )
                        + "\n",
                    )

                # The switch is handed to `_turn` rather than checked out here. Checking it
                # here meant returning out of this loop with the generator suspended mid-body,
                # and an abandoned generator is not a finished one: everything after its last
                # `yield` — the turn-log row saying what the turn spent, the feed's own "done" —
                # never ran. Stopping is the one case where you most want that row.
                for line in _turn(recorder, messages, config, conversation_id, opening, stopping=stopping):
                    live_turns.publish(live, line)
        except Exception as exc:
            # Broad on purpose: this thread is the only one running the turn, and no reader can
            # see an exception raised here — an uncaught one would leave every watcher waiting
            # for an end that never comes.
            live_turns.publish(live, json.dumps({"type": "error", "message": str(exc)}) + "\n")
        finally:
            _disarm(conversation_id, stopping)
            # Anything still waiting belonged to this turn. Carrying it into the next one would
            # put it in front of a model whose recent history no longer matches what it was
            # reacting to.
            steering.forget(conversation_id)
            live_turns.finish(live)  # releases every reader, now and later

    # `copy_context().run` rather than a bare Thread target: everything else this request
    # established in ContextVars — the project, the turn's scratch notes, whether this is
    # the turn's scratch notes — has to travel with it. `turn_notes` keeps a turn to one checkpoint
    # per repo, so losing it would take the checkpoint chain with it.
    threading.Thread(
        target=contextvars.copy_context().run,
        args=(work,),
        name=f"kith-turn-{conversation_id}",
        daemon=True,
    ).start()
    return live


def continue_conversation(conversation_id: str, trigger: str) -> None:
    """Run one turn in `conversation_id`, started by something other than a typed message.

    The scheduler's door onto `begin_turn`, and now nothing more than that. It used to build the
    messages and drive `_turn` itself, which is how every turn Kith began on his own came to be
    unwatchable, unstoppable and unsteerable — see `begin_turn` for how that happened.

    Public, and here rather than in `services/scheduler.py`, which used to reach in and import
    `_build_messages`, `_Recorder` and `_turn` — three *private* functions — out of this
    module. That was the last upward import in the tree. A reminder firing is not a scheduling
    concern that happens to need a turn; it is a turn, started differently, and the turn lives
    here until it moves out of the route entirely.
    """
    conversations.record(AGENT_DB_PATH, conversation_id, "user", trigger)
    live = begin_turn(
        conversation_id,
        default_config(),
        lambda: [*conversations.full_messages(conversation_id), {"role": "user", "content": trigger}],
        trigger,
    )
    # Drained rather than left to run, which keeps the scheduler exactly as serial as it was:
    # `wake_finished` and `fire_due` both loop over conversations calling this, and returning
    # the moment the thread started would set every one of them going at once. The turn is on
    # its own thread either way — this waits for it the same way the first reader of a chat
    # does, through the one watch path, so there is no second way to wait to keep in step.
    #
    # It does mean a long turn holds the timer thread, and nothing else is checked until it
    # ends. That was true before this and is not made worse by it; fixing it is a question
    # about how many turns may run at once, which is not this change.
    for _ in live_turns.watch(live):
        pass


def _tool_block_chars() -> int:
    """How many characters the tool declarations take in a request.

    Measured here because this is an adapter and `kith.tools` is one too — the fold needs the
    number, not the registry, and taking a database path in order to go and total the schemas
    itself is what made `services/history.py` import the adapter layer.
    """
    return sum(len(json.dumps(schema)) for schema in tools.tool_schemas(AGENT_DB_PATH))


class _Recorder:
    """Writes a turn to the transcript as it happens, keeping its shape.

    Deltas are buffered and flushed as blocks rather than written per token: a line per
    token would be a hundred-thousand-line file for one afternoon, and the block is the
    unit anything reading it back wants anyway.

    A block is flushed when the channel changes — reasoning to prose, prose to a tool call —
    which is exactly how the live view decides where one part ends and the next begins. That
    is deliberate: a resumed conversation should look like the one you had, not like a
    transcript of it.
    """

    def __init__(self, conversation_id: str) -> None:
        self.conversation_id = conversation_id
        self.channel = ""
        self.buffer: list[str] = []
        self.said: list[str] = []
        #: The latest context reading, written once when the turn ends. See `saw`.
        self.context: dict = {}
        #: The FIRST reading of the turn — round 1, before this turn's own tool calls added
        #: anything. That makes it the size of everything actually *persisted* going into this
        #: turn: persona, folded brief, prose said so far, tool schemas. Round-to-round growth
        #: within a turn is real but thrown away on the next one (see `history.py`'s docstring
        #: on why tool history isn't replayed) — so `self.context` bounces with how much work a
        #: given turn happened to do, while this climbs steadily with the conversation itself
        #: and is what a meter meant to answer "how much of my history is in here" should show.
        self.baseline_context: dict = {}
        self.folded = False
        #: Rounds sent again. Rides home on the same record as `folded` — both are facts about
        #: how the turn went rather than things it said, and a reopened conversation should read
        #: the same as the live one did.
        self.retried = 0

    def saw(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "delta":
            role = "reasoning" if event.get("role") == "reasoning" else "text"
            if role != self.channel:
                self._flush()
                self.channel = role
            self.buffer.append(event.get("text") or "")
            return
        # Anything else ends whatever block was open, so ordering survives.
        self._flush()
        if kind == "context":
            # Held, not written. One of these lands every round, and each is a *reading* of the
            # window rather than something that happened — so the last one is the only one still
            # true, and forty categorised breakdowns in the transcript would all say what it
            # says. Written once in `finish`.
            if not self.baseline_context:
                self.baseline_context = event.get("context") or {}
            self.context = event.get("context") or {}
            return
        if kind == "compacting":
            # This one IS an event, and a rare one worth keeping: it means the turn ran out of
            # room and paid for a summary. A reopened conversation should show that its middle
            # is notes rather than the original steps.
            self.folded = True
            return
        if kind == "retrying":
            self.retried += 1
            return
        if kind in ("tool_call", "tool_result", "stats"):
            conversations.record_event(self.conversation_id, kind, _readable(event))

    def finish(self, error: str | None = None, stopped: bool = False) -> None:
        self._flush()
        # The window as it stood when the turn ended, and whether it had to fold to get there.
        # Written on the error and stopped paths too: a turn that died is exactly the one whose
        # context reading you want to look at afterwards.
        if self.context:
            conversations.record_event(
                self.conversation_id,
                "context",
                {
                    "context": self.context,
                    "baseline": self.baseline_context,
                    "folded": self.folded,
                    "retried": self.retried,
                },
            )
        if error:
            conversations.record_event(self.conversation_id, "error", {"message": error})
        if stopped:
            conversations.record_event(self.conversation_id, "stopped", {})
        # The whole reply as one message, which is what the next turn's prompt needs. Written
        # on the error and disconnect paths too: an interrupted turn is exactly the one whose
        # half-answer you want to keep.
        text = "".join(self.said).strip()
        if text:
            conversations.record(AGENT_DB_PATH, self.conversation_id, "assistant", text)

    def _flush(self) -> None:
        text = "".join(self.buffer)
        self.buffer = []
        if not text.strip():
            self.channel = ""
            return
        if self.channel == "reasoning":
            conversations.record_event(self.conversation_id, "reasoning", {"text": text})
        elif self.channel == "text":
            conversations.record_event(self.conversation_id, "said", {"text": text})
            self.said.append(text)
        self.channel = ""


def _readable(event: dict) -> dict:
    """One transcript line, with a picture's bytes left out of it.

    The conversation stopped carrying base64 when the image leak was fixed, and the
    transcript went on storing every byte: a six-round turn that looked at one page came to
    360KB, of which 344,943 was a single string.

    That is not a context cost — nothing re-reads it — but it is against the whole point of
    the file. The reason transcripts are plain-text JSONL rather than rows in a table is that
    you can grep them, open them in an editor, and still read them in ten years. One `grep`
    hit that prints 345,000 characters of base64 is none of those things, and it is bytes on
    disk forever for every image he ever looks at.

    Nothing is lost. The path is in the same record, the file is in his folder, and the
    interface only ever tested this field for truthiness to say "looked at it" — it never
    rendered the data URI. So the trail still says which image, when, and that he saw it.
    """
    if event.get("type") != "tool_result":
        return event
    from kith.services.agent_loop import _without_image

    return {**event, "result": _without_image(event.get("result"))}


class _MindFeed:
    """A chat turn, on the record.

    Chat used to leave no trace anywhere except its own transcript: no line on the Mind
    feed, no row in the flight recorder. Every mode the runner has wrote both, and the one
    path where most of the real work happens wrote neither — so "what has he been doing"
    could be answered for some work and not for the afternoon you spent together — a 40k turn
    visible in one place while a 200k one left no trace at all.

    It matters more now that the Mind panel is per session. A conversation you have never
    left working would otherwise show an empty panel forever, which reads as broken rather
    than as "he has not gone off on his own here".

    Deliberately the same shapes the runner emits — `reply` as the head, `tool`, `tokens`,
    `done` — so the panel renders a turn exactly as it renders a step. A second vocabulary
    for the same events would have meant a second renderer.
    """

    def __init__(
        self, conversation_id: str, opening: str = "", stopping: threading.Event | None = None
    ) -> None:
        self.conversation_id = conversation_id
        self.opening = opening
        #: The turn's own stop switch, so a session past its budget can be stopped the same
        #: way a person stops one. The cap used to `rest` the session instead — tell it to
        #: stop keeping going — and there is no keeping going to stop.
        self.stopping = stopping
        self.tools: list[str] = []
        self.rounds = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.tokens_uncached = 0
        self.said = ""
        self.error: str | None = None
        self.started = time.monotonic()
        self._publish("reply", f"you: {opening.strip()[:80]}" if opening.strip() else "you: (attachment)")

    def _publish(self, kind: str, text: str, **fields) -> None:
        try:
            from kith.services.activity import feed

            feed.publish(kind, text, conversation=self.conversation_id, **fields)
        except Exception:
            # A feed line must never be the thing that takes a turn down.
            pass

    def saw(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "tool_call":
            self.tools.append(event["name"])
            self._publish(
                "tool",
                describe_call(event["name"], event.get("arguments") or {}),
                tool=event["name"],
                args=short_args(event.get("arguments") or {}),
            )
        elif kind == "delta" and event.get("role") == "text":
            self.said += event.get("text") or ""
        elif kind == "stats":
            stats = event.get("stats") or {}
            fresh = int(stats.get("uncachedTokens") or 0)
            out = int(stats.get("responseTokens") or 0)
            self.rounds += 1
            self.tokens_in += int(stats.get("promptTokens") or 0)
            self.tokens_out += out
            self.tokens_uncached += fresh
            self._publish(
                "tokens",
                f"{fresh + out:,} tokens",
                tokens={
                    "round": self.rounds,
                    "uncached": fresh,
                    "cached": int(stats.get("cachedTokens") or 0),
                    "out": out,
                },
            )
            # Meter the session, and stop the turn if this round took it past its budget. The
            # message telling the person why is written by `charge_session`; what is left here
            # is the acting on it.
            from kith.services.activity import feed

            if feed.charge_session(self.conversation_id, fresh, float(stats.get("costUsd") or 0.0)):
                if self.stopping is not None:
                    self.stopping.set()
        elif kind == "retrying":
            # The one event in a turn that is otherwise write-only. In the message it is a live
            # line that the error then replaces, so afterwards there is no evidence the retry
            # ran — and against a dead network the whole sequence is over in about six seconds,
            # because name resolution fails in hundredths rather than timing out. So "is the
            # retry working" was unanswerable from the screen while the loop was, in fact,
            # trying six times. Here it is timestamped and it stays.
            self._publish("retrying", f"reconnecting — attempt {int(event.get('attempt') or 0) + 1}")
        elif kind == "error":
            self.error = event.get("message")
            self._publish("error", str(self.error))

    def finish(self) -> None:
        self._publish("done", "turn complete")
        outcome = f"error: {self.error}" if self.error else (self.said.strip()[:280] or "answered")
        try:
            repo.messages.add_turn_log(
                AGENT_DB_PATH,
                clock.now_iso(),
                "chat",
                self.opening.strip()[:80] or None,
                self.tools,
                self.tokens_in,
                self.tokens_out,
                round(time.monotonic() - self.started, 2),
                outcome,
                tokens_uncached=self.tokens_uncached,
            )
        except Exception:
            # Accounting. The turn already happened and the person already has the answer;
            # failing to write down what it cost must not retroactively fail it.
            pass


@api.post("/chat")
@api.input(ChatRequestSchema, arg_name="payload")
@api.doc(
    summary="Stream an agent turn",
    description=(
        "Runs the agentic loop (the model may call its memory/notes/journal/task "
        "tools) and streams `application/x-ndjson`: one JSON object per line.\n"
        '- `{"type":"delta","role":"reasoning"|"text","text":"..."}`\n'
        '- `{"type":"tool_call","id":"...","name":"...","arguments":{...}}`\n'
        '- `{"type":"tool_result","id":"...","name":"...","result":{...}}`\n'
        '- `{"type":"stats","stats":{...}}` (one per model request, so several per turn — '
        "`uncachedTokens` is the prompt with cache hits removed)\n"
        '- `{"type":"error","message":"..."}`\n'
        '- `{"type":"done"}` (terminal)'
    ),
    responses={200: "NDJSON stream of agent events"},
)
def chat(payload):
    config = merge_overrides(default_config(), payload.get("config") or {})
    client_history = payload.get("messages") or []

    # Which conversation this belongs to. Opened on the first message rather than when the
    # window opens, so idly launching the app does not litter the history with empties.
    #
    # Resolved *before* the prompt is built, not after: what this session is working on
    # decides which project's memory he is shown, and building the prompt first meant that
    # question was asked with no session to ask it about.
    conversation_id = str(payload.get("conversationId") or "").strip()
    latest_message = next((m for m in reversed(client_history) if m.get("role") == "user"), None)
    latest = (latest_message or {}).get("content", "") or ""
    resumed = bool(conversation_id)
    if not conversation_id:
        conversation_id = conversations.start(AGENT_DB_PATH, latest)["id"]
    conversations.record(AGENT_DB_PATH, conversation_id, "user", latest)

    # What this turn is being asked, resolved on the turn's own thread rather than here — see
    # `begin_turn`. The branch is the request's business: only a chat has a client copy of the
    # history to reconcile against the one on disk.
    def gather():
        # The client's own copy is prose-only by design (it strips tool calls before
        # ever sending them), so on a resumed conversation everything in it but the
        # message just typed is ignored and the transcript rebuilds the real thing,
        # tool history included. Taken whole rather than just its text, so an
        # attachment riding on it isn't dropped.
        if resumed:
            return [
                *conversations.full_messages(conversation_id),
                latest_message or {"role": "user", "content": latest},
            ]
        return client_history

    live = begin_turn(conversation_id, config, gather, latest)

    def generate():
        # Tell the client which conversation it is in before anything else, so a chat
        # started without an id can attach itself and reload into the same place.
        yield json.dumps({"type": "conversation", "id": conversation_id}) + "\n"
        # Just the first reader. Identical to what `/attach` does for one arriving later —
        # which is the point: there is no separate "resume" path to keep in step.
        yield from live_turns.watch(live)

    return Response(
        generate(),
        mimetype="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _turn(
    recorder: _Recorder,
    messages: list,
    config,
    conversation_id: str,
    opening: str = "",
    *,
    stopping: threading.Event | None = None,
):
    """One turn of the loop, streamed as it happens.

    Split out from the route so the session binding can wrap it in a `with` and every tool
    call inside knows which conversation it belongs to.

    Ends itself, on every path — that is what the `finally` is for. A turn can be over five
    ways: it finished, it reported an error, it died on the way out, someone stopped it, or the
    caller gave up on the generator. Two of them each used to close the books at their own site
    and the rest closed nothing at all, which is why a stopped turn left no turn-log row and a
    feed that never said "done". One exit means the recorder and the feed are finished exactly
    once, whoever decided the turn was over.

    `stopping` is read between events, which is the only place a turn can be interrupted
    without abandoning a tool call half-done. `agent_loop` has no cancellation hook of its own,
    so this is as fine-grained as stopping gets, and it is enough: the wait is one tool call,
    not the rest of the turn. None means nothing can stop this one — which is the reminder
    path in `services.scheduler`, where there is no one to click anything.
    """
    watcher = _MindFeed(conversation_id, opening, stopping=stopping)
    stopped = False
    error: str | None = None
    try:
        for event in stream_agent(
            messages,
            config,
            ollama_host(),
            AGENT_DB_PATH,
            # The loop is handed what it needs from the tool layer rather than importing it.
            # `kith.tools` is an adapter like this module, and an adapter is the right place to
            # reach for another one; a service reaching down for it was the arrow pointing the
            # wrong way. Built once per turn, which is also where the language-server question
            # and the database path get answered once — both are inputs to the cached prompt
            # prefix and must not change under a turn.
            tools.host(AGENT_DB_PATH),
            conversation_id=conversation_id,
            # Anything said while this turn is running, collected at the next round boundary.
            # A callable rather than the store itself: taking new input is this module's
            # business, and a loop that imported the queue would be the loop deciding where
            # messages come from.
            steer=lambda: steering.take(conversation_id),
            # Rounds stay on the declared knob (max_rounds, 40). A conversation wants real
            # room: you are here, so a long turn is one you can watch and stop.
            #
            # `expect_durable` stays off, and that was learned the hard way an hour after
            # turning it on. Work that leaves nothing behind really is a failure — the
            # whole point of one is to make progress nobody asked to watch. But a
            # conversation is not that, and cannot be told apart upfront: "what have you
            # been working on" is answered by answering it. With durability demanded, he
            # replied honestly that the board was empty and then wrote
            # `session-findings-2026-07-31.md` to satisfy the rule — a file nobody wanted,
            # about nothing, because the harness insisted on an artefact.
            #
            # The distinction that matters is not which path the work came in on. It is "asked to do
            # something" versus "asked something", and the transport does not know which
            # it is carrying. So the directive above asks him to do the work, and nothing
            # forces him to manufacture evidence of having done it.
        ):
            recorder.saw(event)
            watcher.saw(event)
            # Scrubbed on the way out too, not only on the way into the transcript. The model
            # has not been sent base64 since the image leak was fixed and the transcript
            # stopped storing it shortly after — but this line went on streaming the whole data
            # URI to the browser, where the generic result renderer printed it. So looking at
            # the interface showed forty thousand characters of base64 sitting in a tool result,
            # which is indistinguishable from the bug that is actually fixed, and reasonable
            # grounds to think it was back.
            #
            # It is a real cost as well as a misleading one: 44KB per image over the wire and
            # into the DOM, for a string nothing on the other side can use. The interface only
            # ever tested the field for truthiness to say "looked at it".
            yield json.dumps(_readable(event)) + "\n"
            if event.get("type") == "error":
                error = event.get("message")
                return
            if stopping is not None and stopping.is_set():
                stopped = True
                return
    except Exception as exc:
        # A turn that died on the way out — the provider hung up, the socket broke — rather than
        # one that reported an error event. Named here rather than left to propagate, because
        # `finally` below closes the books either way, and with nothing set it would close them
        # as though he had simply answered. The feed is told in the shape it already understands,
        # so the row reads the same as any other failed turn.
        error = str(exc)
        watcher.saw({"type": "error", "message": error})
        raise
    finally:
        # The single exit. Reached by all four ways a turn ends — it finished, it errored,
        # someone stopped it, or the reader gave up and closed the generator — so the books are
        # closed exactly once and no path can forget to do it.
        recorder.finish(error=error, stopped=stopped)
        watcher.finish()
    # After the `finally`, so the order the client sees is unchanged: the row is written and
    # the feed is closed, and only then does the stream say it is over.
    yield json.dumps({"type": "done"}) + "\n"
