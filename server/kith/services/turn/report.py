"""What is in the window, and what filled it — the numbers behind the context screen.

Not a route and not the turn. This is the reporting layer: everything that reads a finished
conversation back and describes the request a turn would send from it, message by message, with
what each one costs. It lived in `api/routes/chat.py` beside the turn engine, which is how a
screen you open once a month came to share a file with the loop that answers every message.

**Nothing here may spend money.** A fold is a summarisation call, so reproducing the folded list
is not an option — every figure below is either the stored reading, or `message_chars` over the
ratio that reading was costed with. A screen you open in order to look at something must never
pay to draw itself.

**Nothing here re-derives what is already known.** The totals are the stored reading's, untouched.
Recomputing them would need the schemas as they will be narrowed next turn, the live block as it
will be written, and the ratio the provider's own billing calibrated — four things to get right in
order to restate a number the meter is already showing correctly, and getting any of them slightly
wrong produces a detail screen that quietly disagrees with the rail beside it, with no way for the
person to tell which of the two is lying.

`tool_chars` is passed in rather than measured. Totalling the tool declarations means reaching for
`kith.tools`, which is an adapter — the same reach that once made `services/history.py` import the
adapter layer. The caller is an adapter and already has the number.
"""

from __future__ import annotations

import json
from collections import Counter

from kith.config import default_config
from kith.llm import ledger
from kith.llm.budget import SEED_CHARS_PER_TOKEN, message_chars
from kith.services import conversations
from kith.services.turn.prompt import as_sent as prompt_as_sent


def reading_after_fold(conversation_id: str, was: ledger.Ledger, now: ledger.Ledger) -> dict | None:
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
    _, reading, ratio = last_reading(conversation_id)
    if not reading.get("lines"):
        return None
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


#: How much of a message the list carries. Enough to recognise a row; not enough to make the
#: response large. The whole text is one request away, for the one you click.
_PREVIEW_CHARS = 240


def last_reading(conversation_id: str) -> tuple[dict, dict, float]:
    """The reading the meter is showing, and the ratio it was costed with.

    Three lines that have to agree and were written out twice — once in `reading_after_fold`
    and once in `context_detail` — which is two chances to fall out of step on a screen whose
    whole job is to be trusted about numbers.

    `baseline` is the fallback because a turn records both: the reading at the end and the one
    from round 1, and a conversation whose last turn never got past its first round has only the
    second. The seed ratio is for readings recorded before `charsPerToken` was sent with them;
    a fold typically removes hundreds of thousands of characters, so the divisor is the whole
    difference between "the meter moved by the right amount" and "the meter moved".
    """
    taken = conversations.latest_reading(conversation_id)
    reading = taken.get("context") or taken.get("baseline") or {}
    ratio = float(reading.get("charsPerToken") or 0) or SEED_CHARS_PER_TOKEN
    return taken, reading, ratio


def _one_line(text: str) -> str:
    """Collapsed to a single scannable line, cut to the length a list row can hold."""
    return " ".join(str(text).split())[:_PREVIEW_CHARS]


def _sent(conversation_id: str, tool_chars: int) -> tuple[list[dict], bool]:
    """The list a turn would send, and whether a fold is owed before it does."""
    messages = conversations.full_messages(conversation_id)
    return prompt_as_sent(messages, default_config(), conversation_id, tool_chars=tool_chars)


def _previous(conversation_id: str, tool_chars: int) -> list[dict] | None:
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
    return prompt_as_sent(messages[:cut], default_config(), conversation_id, tool_chars=tool_chars)[0]


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


def _as_sent(conversation_id: str, ratio: float, tool_chars: int) -> dict:
    """The prompt, message by message, costed the way the ledger costs it.

    Same `message_chars` and same ratio as the categories above, so a row's tokens and the
    category it lands in are the same measurement rather than two that nearly agree.
    """
    messages, fold_pending = _sent(conversation_id, tool_chars)
    previous = _previous(conversation_id, tool_chars)

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
        bucket = by_role.setdefault(row["role"], {"role": row["role"], "tokens": 0, "count": 0})
        bucket["tokens"] += row["tokens"]
        bucket["count"] += 1
    total = sum(row["tokens"] for row in listed)
    for bucket in by_role.values():
        bucket["share"] = round(bucket["tokens"] / total, 4) if total else 0.0

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
                "args": _one_line(args),
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
    return _one_line(text)


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


def detail(conversation_id: str, tool_chars: int) -> dict:
    """The reading the meter is showing, plus which calls actually filled it.

    **Its own endpoint rather than more fields on the streamed `context` event.** That event is
    emitted every turn and persisted into the transcript — 167 of them on one real conversation
    here — so hanging two and a half thousand items off each would grow every stored turn forever
    to serve a screen that is open for a few seconds a month, and would charge that cost to
    everyone who never opens it. A reading is streamed; a breakdown is asked for.

    **The items are the conversation, not the window, and the response keeps them apart.** A
    reading measures the request a turn actually sent, which is the conversation *after* the fold
    — old turns replaced by a brief, their tool results gone with them. `full_messages` is the
    transcript: everything that ever happened. On one real conversation here those are 556,392
    and 2,361,997 tokens, so the window is a quarter of the history.

    The first version of this treated the items as a breakdown *of* the reading and printed a
    repeat figure worth "121% of the window", which is how the confusion announced itself. Both
    figures are worth having: `lines`/`used` are the window and agree with the meter; the items
    and `wasted` are the conversation, totalled separately in `itemsTotal` so nothing has to
    express one as a share of the other. The items are still the actionable half — a file read
    fifty-one times is a habit that will refill the window whether or not those particular copies
    survived the last fold.
    """
    taken, reading, ratio = last_reading(conversation_id)
    items = ledger.itemise(conversations.full_messages(conversation_id), chars_per_token=ratio)
    return {
        # False on a conversation that has never had a turn. The screen needs to say "nothing has
        # measured this yet" rather than draw an empty window at 0%, which reads as an answer and
        # is not one.
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
        # The items' own denominator. Without it the screen has nothing to express `wasted` as a
        # share of except `used`, which is a different measurement and gave "121%".
        "itemsTotal": sum(item.tokens for item in items),
        # ── the prompt itself, message by message ──
        "sent": _as_sent(conversation_id, ratio, tool_chars),
        # What the provider actually billed for the last round. Everything else on this screen is
        # `message_chars` over a ratio; these came back from the provider.
        "lastRound": _last_round(conversation_id),
    }


def one_message(conversation_id: str, index: int, tool_chars: int) -> dict:
    """One message in full, fetched when someone clicks the row.

    Its own request because the list must stay small. One real conversation here is 2.36M tokens
    of transcript; carrying every message's text in order to draw a list of previews would be a
    several-megabyte response for a screen that shows one at a time.
    """
    messages, _ = _sent(conversation_id, tool_chars)
    if not 1 <= index <= len(messages):
        return {"index": index, "role": "", "tool": "", "text": ""}
    message = messages[index - 1]
    content = message.get("content")
    if isinstance(content, list):
        text = "\n\n".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    else:
        text = str(content or "")
    if not text.strip() and message.get("tool_calls"):
        text = json.dumps(message["tool_calls"], indent=2)
    return {
        "index": index,
        "role": str(message.get("role") or ""),
        "tool": str(message.get("tool_name") or ""),
        "text": text,
    }
