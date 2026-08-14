"""Talking to him."""

from __future__ import annotations

import base64
import contextvars
import itertools
import json
import re
import threading
import time
from pathlib import Path

from flask import Response, jsonify

from kith import tools
from kith.api.blueprint import api
from kith.config import (
    default_config,
    merge_overrides,
    model_capabilities,
    ollama_host,
)
from kith.infra import permissions
from kith.infra import workspace as sandbox
from kith.infra.db import repositories as repo
from kith.kernel import clock, live_turns, session_context
from kith.llm import ledger
from kith.llm.budget import SEED_CHARS_PER_TOKEN
from kith.schemas import (
    AnswerSchema,
    ChatRequestSchema,
)
from kith.services import conversations, history, memory_context, questions, touched
from kith.services.activity import describe_call, short_args
from kith.services.agent_loop import stream_agent
from kith.settings import AGENT_DB_PATH

#: What a conversation is for.
#:
#: This used to open with "CAPTURE, DON'T DO (most important)" — asked to build something, he
#: was to file a task, refuse to touch a work tool, and say roughly when he would get to it.
#: The intent was sound when a conversation could not carry a long job: it was the
#: wrong place for a long job, so it became an intake desk.
#:
#: The cost was that everything consequential happened at the intake desk anyway. Laying out a
#: project, deciding a roadmap, choosing what to build first — all of it lands in a
#: conversation, and a conversation was the one path with no round budget worth the name, no
#: loop detection, and no row in any log. He would file the work correctly and then be unable
#: to lift a finger, which reads as an assistant that does nothing.
#:
#: So a conversation is now where the work happens. He has the same tools, the same limits and
#: the same discipline he has when nobody is watching, and the difference between the two is
#: only that here you are present to redirect him.
#:
#: Points 3 and 4 are the other half of "do the work", and they were added after watching the
#: opposite failure. 3 used to read only "ASK WHEN IT WOULD CHANGE WHAT YOU BUILD" — being stuck
#: does not change what you build, so a turn that needed something had no sanctioned way to say
#: so and wrote a paragraph instead. 4 is the specific shape that keeps recurring: he states a
#: condition ("I won't mark it done until the evidence is in the logs"), and then treats it as
#: something that will happen to him rather than something to go and look at. Measured on two
#: consecutive turns with the full toolset and nothing narrowed: zero tool calls, 409,337 prompt
#: tokens, and the evidence already thirty-one minutes old in a log he never opened. The turn
#: after — same question, pushed — found it in three calls. See
#: `tests/test_being_blocked_is_a_question_not_a_stop.py`.
CHAT_DIRECTIVE = (
    "Your person is here, in this conversation, and can redirect you. That is the only thing "
    "this note adds to what you already know about how you work.\n\n"
    "1. DO THE WORK. When they hand you something, start on it. Not 'I've noted that and I'll "
    "get to it' — read the file, run the command, make the change, and show them what "
    "happened.\n"
    "2. GIVE IT A HOME FIRST, if it is more than one sitting. A project and its first "
    "milestone's tasks, so the work survives being put down — the `running-a-project` skill is "
    "how. Say the plan back before it runs, so they can fix it. Then start on the first task "
    "in the same breath.\n"
    "3. ASK WHEN IT WOULD CHANGE WHAT YOU BUILD, and ask when you are BLOCKED. They are right "
    "here, so a real question costs almost nothing. A guess dressed as a decision costs the "
    "whole task — and so does going quiet. Use `ask`; it holds the turn open for the answer.\n"
    "4. A CONDITION YOU STATE IS A CONDITION YOU CHECK. 'I won't call it done until X' and 'I "
    "still need to confirm Y' are your next action, not something you are waiting on. Go and "
    "look — read the log, run the query, list the directory. Only if looking is genuinely not "
    "something you can do is it a question for them, and then it is `ask`, not a paragraph. "
    "Ending your turn with an unchecked condition is the one way to make no progress at all."
)


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
_RUNNING: dict[str, threading.Event] = {}
#: Held across read-then-write on `_RUNNING`. The turns contending for it are on separate
#: threads by design, so "check whether this is still mine, then remove it" has to be one step.
_RUNNING_LOCK = threading.Lock()


def _arm(conversation_id: str) -> threading.Event:
    """The switch for a turn about to start, and the one it must read for the rest of its life.

    Returned rather than looked up again later. A turn that re-reads `_RUNNING[id]` between
    events is reading whichever turn started most recently, which is how one click stopped two.
    """
    event = threading.Event()
    with _RUNNING_LOCK:
        _RUNNING[conversation_id] = event
    return event


def _disarm(conversation_id: str, event: threading.Event) -> None:
    """Forget a finished turn's switch — but only if it is still the current one.

    The `if` is the whole point. An unconditional `pop` meant the first turn to *finish*
    deleted the entry a still-running turn was registered under, and Stop then answered
    `{"stopping": false}` for a turn visibly in progress.
    """
    with _RUNNING_LOCK:
        if _RUNNING.get(conversation_id) is event:
            del _RUNNING[conversation_id]


def _current(conversation_id: str) -> threading.Event | None:
    with _RUNNING_LOCK:
        return _RUNNING.get(conversation_id)


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
    event = _current(conversation_id)
    if event is not None:
        event.set()
    # Now nothing that wakes up can get past its next check, so it is safe to wake it.
    questions.release(conversation_id)
    permissions.release_waiting()
    return event is not None


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


@api.post("/chat/<conversation_id>/stop")
@api.doc(
    summary="Stop the turn running in a conversation",
    description="Asks it to stop after the event it is on. Returns whether there was one.",
)
def stop_turn(conversation_id: str):
    return jsonify({"stopping": _stop(conversation_id)})


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


def continue_conversation(conversation_id: str, trigger: str) -> None:
    """Run one turn in `conversation_id`, started by something other than a typed message.

    Everything downstream of the trigger is the machinery a real chat turn uses, which is the
    whole point: the result becomes an actual message in the transcript rather than a line in
    a live feed that is gone the moment nobody is looking at it.

    Public, and here rather than in `services/scheduler.py`, which used to reach in and import
    `_build_messages`, `_Recorder` and `_turn` — three *private* functions — out of this
    module. That was the last upward import in the tree. A reminder firing is not a scheduling
    concern that happens to need a turn; it is a turn, started differently, and the turn lives
    here until it moves out of the route entirely.
    """
    config = default_config()
    history = [*conversations.full_messages(conversation_id), {"role": "user", "content": trigger}]
    messages = _build_messages(history, config, conversation_id)
    conversations.record(AGENT_DB_PATH, conversation_id, "user", trigger)

    recorder = _Recorder(conversation_id)
    for _ in _turn(recorder, messages, config, conversation_id, opening=trigger):
        pass  # driving the generator is the point — nothing is streaming this anywhere


def _tool_block_chars() -> int:
    """How many characters the tool declarations take in a request.

    Measured here because this is an adapter and `kith.tools` is one too — the fold needs the
    number, not the registry, and taking a database path in order to go and total the schemas
    itself is what made `services/history.py` import the adapter layer.
    """
    return sum(len(json.dumps(schema)) for schema in tools.tool_schemas(AGENT_DB_PATH))


def _conversation_chars(messages) -> int:
    """How much prose a message list carries — the number the fold is deciding about."""
    return sum(len(str(m.get("content") or "")) for m in messages)


def _build_messages(messages, config, conversation_id: str = "", _folded: dict | None = None):
    """The persona, then the turns, then the state he is in right now.

    The order is a caching decision, and it is worth more than it looks. Everything a
    provider can reuse has to sit in an unchanging prefix: the persona and the turn
    directive never change, so they go first and alone.

    What follows them changes constantly — the clock to the minute, his mood, how long
    since he last acted, whatever memory is present — and it used to be concatenated into
    the same system message. That is what made a repeated "hey" cost full price twice:
    the moment he replies, `last_activity_at` moves, "1 hour ago" becomes "just now", and
    the message is no longer byte-identical. Providers that cache automatically match at
    message granularity, so one changed word at the end discarded ~8,000 cacheable tokens
    at the start. Measured: identical consecutive messages, 0% cached; with the volatile
    part moved out, 99.7%.

    Putting it last is also the better prompt. It is the freshest thing he knows, and the
    directive already sat at the end for exactly that reason.
    """
    out = []
    persona = (config.system or "").strip()
    if persona:
        # Byte-identical on every request Kith ever makes. Nothing else may join it.
        out.append({"role": "system", "content": f"{persona}\n\n{CHAT_DIRECTIVE}".strip()})
    # Fold the older turns of a long conversation into a running brief before replaying them,
    # so the prompt stops growing without bound. The brief sits here deliberately — after the
    # cached persona, before the recent turns — so it never disturbs the stable prefix, and it
    # is persisted so a fold is not re-run every turn. Below the size threshold this returns the
    # history untouched, so a short conversation is byte-for-byte what it was.
    folded, fresh = history.fold(
        messages, config, conversation_id, ollama_host(), tool_chars=_tool_block_chars()
    )
    if fresh is not None and conversation_id:
        conversations.record_summary(conversation_id, fresh["through"], fresh["text"])
    # Reported rather than inferred by the caller. Comparing what went in against what comes
    # out cannot see a fold: this function also prepends the persona and appends the present
    # state, so a heavily folded turn can still produce a longer list than it was given.
    if _folded is not None:
        _folded.update(
            happened=fresh is not None or len(folded) < len(messages),
            fromChars=_conversation_chars(messages),
            toChars=_conversation_chars(folded),
        )
    for message in folded:
        role = message.get("role")
        if role == "system":
            # The folded brief. Passed straight through — it carries no attachments.
            out.append({"role": "system", "content": str(message.get("content") or "")})
            continue
        if role == "assistant" and message.get("tool_calls"):
            # A replayed tool call from an earlier turn (see `conversations.full_messages`).
            # Nothing to attach and no empty-content check applies — it carries no `content`
            # at all, that's not the same as having nothing to say.
            out.append(
                {"role": "assistant", "content": message.get("content"), "tool_calls": message["tool_calls"]}
            )
            continue
        if role == "tool":
            # A replayed tool result. Passed straight through, same reason.
            out.append(
                {
                    "role": "tool",
                    "tool_name": message.get("tool_name", ""),
                    "content": message.get("content", ""),
                }
            )
            continue
        if role not in ("user", "assistant"):
            continue
        # An assistant turn with nothing in it carries no information and is refused by some
        # providers outright — "the message at position N with role 'assistant' must not be
        # empty". Nothing writes one today (`record` skips empty text), but a conversation
        # that acquired one from any source would be *permanently* unusable: every later
        # message rebuilds the same history and fails the same way, with nothing on screen
        # to say why. Skipping it costs nothing and cannot be the wrong call.
        if message.get("role") == "assistant" and not str(message.get("content") or "").strip():
            continue
        out.append(_with_attachments(message))
    now = _present_state(conversation_id)
    if now:
        # `_live` is for the ledger, not the provider — `openai_compat._to_openai` rebuilds
        # every message from role and content alone, so nothing internal can reach a host.
        # It earns its own line because this block is the one region rewritten every turn,
        # and therefore the one place where new context is nearly free to add: everything
        # ahead of it stays cached. Counted inside "System prompt", a block that has grown
        # to ten thousand tokens is indistinguishable from a large persona.
        out.append({"role": "system", "content": now, "_live": True})
    return out


def _with_attachments(message: dict) -> dict:
    """Turn a message with attachments into something he can actually use.

    Every attachment is written into his folder first, and *then* the question of what the
    model can see is asked. That order is the whole design.

    The old version got both halves wrong. Images were inlined without checking whether the
    model had vision at all — the docstring claimed it checked and the code did not. And a
    non-image was reduced to "[They attached: report.pdf. Read it with your own tools.]" with
    no path, no bytes, and nothing written anywhere: he was told to open a file that did not
    exist. Meanwhile the composer only accepted `image/*`, so the file branch could not run
    even in principle.

    Writing it down first means the fallback is real. A model with no vision still gets told
    about the picture and where it is, and he can open it with his own tools — `sips` for its
    size, python for its pixels — which is a worse answer than seeing it and a much better one
    than the attachment silently evaporating.
    """
    text = message.get("content", "") or ""
    attachments = [a for a in (message.get("attachments") or []) if isinstance(a, dict)]
    if not attachments:
        return {"role": message["role"], "content": text}

    saved: list[tuple[dict, str]] = []
    for attachment in attachments:
        try:
            saved.append((attachment, _save_attachment(attachment)))
        except Exception as exc:
            saved.append((attachment, f"(could not be saved: {exc})"))

    can_see = bool(model_capabilities().get("images"))
    inline = [a for a, _ in saved if str(a.get("kind")) == "image" and a.get("data")] if can_see else []

    lines = []
    for attachment, where in saved:
        name = str(attachment.get("name") or "a file")
        seeing = " (shown to you below)" if attachment in inline else ""
        lines.append(f"- `{where}`{seeing}" if where.startswith("inbox/") else f"- {name} {where}")
    note = "They attached:\n" + "\n".join(lines)
    if not can_see and any(str(a.get("kind")) == "image" for a, _ in saved):
        note += "\n\nYou cannot be shown images with this model, so open it yourself if it matters."
    text = f"{text}\n\n{note}".strip()

    # Text is given, not referred to.
    #
    # Everything above answers "where is this file" — the right answer for a PDF or a spreadsheet,
    # which he opens with his own tools because he has a computer. It is the wrong answer for text.
    # The composer lifts a large paste out of the message and carries it alongside as a file, so
    # pointing at the path would mean the log someone just handed him costs a tool call to read,
    # on the one kind of attachment whose entire content is already in the request.
    for attachment, where in saved:
        block = _text_block(attachment, where)
        if block:
            text = f"{text}\n\n{block}".strip()

    if not inline:
        return {"role": message["role"], "content": text}
    parts: list[dict] = [{"type": "text", "text": text}] if text else []
    parts += [{"type": "image_url", "image_url": {"url": str(image["data"])}} for image in inline]
    return {"role": message["role"], "content": parts}


#: How much of a text attachment rides inside the message.
#:
#: Not a limit on what can be attached — the file is written to disk whatever its size, and the
#: path is always given. This is a limit on what is spent carrying it in the prompt. Twenty
#: thousand characters is a few thousand tokens: enough for a stack trace, a config, a long
#: instruction, or most of a source file, and small enough that pasting something enormous costs
#: a fraction of the window rather than the whole conversation.
TEXT_INLINE_CHARS = 20_000


def _text_block(attachment: dict, where: str) -> str:
    """A text attachment's actual contents, fenced and named — or "" if this is not one.

    Truncation says so, out loud and in the same breath as the path.
    A silently shortened file is the worst version of this: he reads what he was given, believes
    it is the whole thing, and answers confidently about a config whose second half he never saw.
    """
    if str(attachment.get("kind")) == "image":
        return ""
    media = str(attachment.get("mediaType") or "")
    if not (media.startswith("text/") or media in ("application/json", "application/xml")):
        return ""

    data = str(attachment.get("data") or "")
    if not data:
        return ""
    try:
        payload = data.split(",", 1)[1] if data.startswith("data:") and "," in data else data
        body = base64.b64decode(payload, validate=False).decode("utf-8", errors="replace")
    except Exception:
        # Undecodable means it was never really text. The path above still stands, and he can open
        # it however he likes — which is a better answer than a block of replacement characters.
        return ""
    if not body.strip():
        return ""

    name = str(attachment.get("name") or "attachment")
    if len(body) > TEXT_INLINE_CHARS:
        kept = body[:TEXT_INLINE_CHARS]
        return (
            f"`{name}` — the first {TEXT_INLINE_CHARS:,} characters of {len(body):,}. "
            f"The whole file is at `{where}`; read it if the rest matters.\n"
            f"```\n{kept}\n```"
        )
    return f"`{name}`:\n```\n{body}\n```"


#: Where attachments land. Inside his folder on purpose: writing there needs no permission,
#: the path is short enough to type in a shell command, and it is somewhere a person would
#: think to look. Named for what it is rather than hidden.
ATTACHMENT_DIR = "inbox"


def _save_attachment(attachment: dict) -> str:
    """Write one attachment into his folder and return the relative path.

    Relative, because the persona tells him to prefer relative paths and to write them in
    backticks — which makes the path a link his person can click, so an attachment they sent
    is one they can also open again from the reply.
    """
    data = str(attachment.get("data") or "")
    if not data:
        raise ValueError("no data was sent")
    payload = data.split(",", 1)[1] if data.startswith("data:") and "," in data else data
    raw = base64.b64decode(payload, validate=False)

    # Their filename, not a generated one — he is going to talk about this file to them, and
    # "inbox/receipt-march.pdf" is a thing they recognise. Stripped of anything that could
    # walk out of the folder.
    name = Path(str(attachment.get("name") or "attachment")).name
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip() or "attachment"

    folder = sandbox.root() / ATTACHMENT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    if target.exists() and target.read_bytes() != raw:
        # A second, different file with the same name. Overwriting would silently replace
        # something they sent earlier and might still be talking about.
        stem, suffix = target.stem, target.suffix
        for n in itertools.count(2):
            candidate = folder / f"{stem}-{n}{suffix}"
            if not candidate.exists() or candidate.read_bytes() == raw:
                target = candidate
                break
    target.write_bytes(raw)
    return f"{ATTACHMENT_DIR}/{target.name}"


def _present_state(conversation_id: str = "") -> str:
    """Everything about him that is true only at this moment."""
    blocks = [
        memory_context.self_block(AGENT_DB_PATH),
        memory_context.presence_block(AGENT_DB_PATH),
        memory_context.people_block(AGENT_DB_PATH),
        memory_context.messages_block(AGENT_DB_PATH),
        memory_context.projects_block(AGENT_DB_PATH),
        memory_context.work_block(AGENT_DB_PATH),
    ]
    present = memory_context.context_block(AGENT_DB_PATH)
    if present:
        blocks.append(f"[Your memory right now]\n{present}")
    # What he knows about the project he is in. Injected rather than fetched, deliberately:
    # a file he has to remember to open is a file he will not open, which is the shape of
    # nearly every failure this codebase has a comment about.
    blocks.append(_project_memory_block(conversation_id))
    # Last, so it is the closest thing to what was just asked. Everything above is about him;
    # this is the only part that is about the conversation, and it is the part that stops him
    # opening a file he has already read — measured at 54% of every read he makes.
    blocks.append(touched.manifest(AGENT_DB_PATH, conversation_id))
    return "\n\n".join(block for block in blocks if block).strip()


def _project_memory_block(conversation_id: str = "") -> str:
    """`.kith/memory.md` for whatever this session is working on.

    Asked of the session, not of the board. The first version looked for "the only active
    project with a folder", which is a guess that gives the right answer exactly until there
    are two — and two at once is the point of sessions, so it was a guess with a deadline.

    Falls back to the single-active-project case for a conversation that has not adopted a
    project yet, because a session usually acquires one part-way through rather than at the
    start, and until it does the one open project is very probably the one being discussed.
    """
    from kith.services import project_memory

    try:
        project = None
        if conversation_id:
            bound = repo.conversations.project_of(AGENT_DB_PATH, conversation_id)
            if bound:
                project = repo.projects.get_project(AGENT_DB_PATH, bound)
        if project is None:
            active = [
                row
                for row in repo.projects.list_projects(AGENT_DB_PATH)
                if row.get("status") == "active" and row.get("directory")
            ]
            if len(active) != 1:
                return ""
            project = active[0]
    except Exception:
        return ""
    if not project or not project.get("directory"):
        return ""
    return project_memory.block(project["directory"], project.get("name") or "")


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
                # The client's own copy is prose-only by design (it strips tool calls before
                # ever sending them), so on a resumed conversation everything in it but the
                # message just typed is ignored and the transcript rebuilds the real thing,
                # tool history included. Taken whole rather than just its text, so an
                # attachment riding on it isn't dropped.
                if resumed:
                    history_messages = [
                        *conversations.full_messages(conversation_id),
                        latest_message or {"role": "user", "content": latest},
                    ]
                else:
                    history_messages = client_history
                folded: dict = {}
                messages = _build_messages(history_messages, config, conversation_id, folded)
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
                for line in _turn(recorder, messages, config, conversation_id, latest, stopping=stopping):
                    live_turns.publish(live, line)
        except Exception as exc:
            # Broad on purpose: this thread is the only one running the turn, and no reader can
            # see an exception raised here — an uncaught one would leave every watcher waiting
            # for an end that never comes.
            live_turns.publish(live, json.dumps({"type": "error", "message": str(exc)}) + "\n")
        finally:
            _disarm(conversation_id, stopping)
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
