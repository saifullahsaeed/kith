"""Making room in a turn's history, and the mutations that do it.

Everything here rewrites `convo` in place to fit the next request into the window. Lifted out
of `agent_loop` because none of it needs the round loop: each function takes the conversation
and returns whether it changed anything.

The one that did not come is `_summarise`, which needs a `Config`, a host and the transport —
it is a model call wearing a compaction function's clothes, and it stays where the calls are.

Read `llm/ledger.py` and `services/compaction.py` alongside this: the ledger is what decides
these are needed, and `compaction.fold` is the once-per-turn alternative to shaving that these
functions are the fallback for.
"""

from __future__ import annotations

import json
from typing import Any

from kith.services import tuning
from kith.services.turn import coverage

#: Arguments that can be an entire file. Worth everything while the call is being made and
#: nothing once it has run — the result already says whether it worked.
#: The argument fields that carry bulk rather than meaning. Measured over eighty transcripts:
#: `write_file.content` 4.9M characters, `edit_files` edits' `new` 1.5M and `old` 0.3M, `shell`
#: `command` 1.5M, `run_workflow.script` 0.25M — and after those, nothing else above 0.1M.
_BULKY_ARGS = ("content", "new", "old", "command", "script")


def stub_arguments(value: Any, stub_chars: int, where: str, *, key: str = "") -> Any:
    """A call's arguments with every bulky string over ``stub_chars`` replaced by a note.

    Walks nested lists and dicts, because the second-largest of them is nested: `edit_files`
    carries its edits as a list, and a stub that only looked at top-level keys trimmed
    `write_file` and let every diff through. Byte-stable on purpose — no age, no count — so a
    call stubbed once is stubbed identically every time after. ``where`` says when it was sent.
    """
    if isinstance(value, dict):
        return {k: stub_arguments(v, stub_chars, where, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [stub_arguments(v, stub_chars, where, key=key) for v in value]
    if isinstance(value, str) and key in _BULKY_ARGS and len(value) > stub_chars:
        return f"…[{len(value):,} characters, sent {where}]"
    return value


#: What replaces an exchange dropped for room. Byte-stable on purpose — no count, no token
#: figure. A note reading "4 earlier steps were dropped" becomes "5 earlier steps" next time
#: and invalidates the whole prefix from that point on every single round, which is the exact
#: bug the compaction code sits next to a comment about.
_DROPPED_NOTE = (
    "(Earlier steps of this turn were set aside to make room. Their results are gone from "
    "this conversation — what you wrote down survives, so work from your notes and files.)"
)


def _drop_oldest_exchange(convo: list[dict[str, Any]]) -> bool:
    """Make room by forgetting the turn's oldest complete exchange. True if one went.

    An *exchange* is one assistant message that called tools plus the tool results that
    answered it. They go together or not at all: a provider rejects a `tool` message whose
    `tool_call_id` has no matching call, so dropping half would turn "running out of room"
    into a 400 with no obvious cause.

    The leading messages are never touched. Those are the persona and the request, and the
    persona is the whole of what the prompt cache holds — dropping it would free a few
    thousand tokens and cost the cached prefix on every remaining round.
    """
    first_exchange = next(
        (i for i, m in enumerate(convo) if m.get("role") == "assistant" and m.get("tool_calls")),
        None,
    )
    if first_exchange is None:
        return False

    end = first_exchange + 1
    while end < len(convo) and convo[end].get("role") == "tool":
        end += 1
    # Leave at least one exchange in place: a turn with no evidence of what it just did is
    # worse than one that is slightly over budget, and the next round would drop the round
    # that was about to save the work.
    if not any(m.get("role") == "assistant" and m.get("tool_calls") for m in convo[end:]):
        return False

    already_noted = any(m.get("_dropped") for m in convo[:first_exchange])
    convo[first_exchange:end] = (
        [] if already_noted else [{"role": "user", "content": _DROPPED_NOTE, "_dropped": True}]
    )
    return True


def _compact_call_arguments(convo: list[dict[str, Any]]) -> None:
    """An old write keeps its filename and lets go of the file.

    The third channel into the conversation, and the last one nothing pruned. `read_file` and
    `shell` cap their *output* at 8,000 characters, and the compactor trims those results as
    they age — but `write_file`'s `content` argument IS the file, and it travels on the
    assistant message rather than the tool result. So writing a 40KB file put 40KB in the
    conversation for every remaining round of the turn, past every limit in the system,
    because none of them are looking at the calls.

    Safe to drop for the same reason a seen picture is: by the time it ages out, the call has
    run and its result is right there saying so. The arguments stay valid JSON with the path
    intact, so he can still see what he wrote and where.
    """
    keep_whole = tuning.value("keep_full_tool_results")
    stub_chars = tuning.value("tool_stub_chars")
    calls = [i for i, m in enumerate(convo) if m.get("role") == "assistant" and m.get("tool_calls")]
    for i in calls[:-keep_whole] if keep_whole > 0 else calls:
        for call in convo[i].get("tool_calls") or []:
            function = call.get("function") or {}
            raw = function.get("arguments")
            if not isinstance(raw, str) or len(raw) <= stub_chars:
                continue
            try:
                args = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(args, dict):
                continue
            stubbed = stub_arguments(args, stub_chars, "earlier in this turn")
            if stubbed != args:
                function["arguments"] = json.dumps(stubbed)


def _compact_images(convo: list[dict[str, Any]]) -> None:
    """Let go of pictures he has already looked at.

    An image rides as its own user message, and `_compact_tool_history` only ever walked
    messages with ``role == "tool"`` — so a picture, once in, re-sent in full on every
    remaining round of the turn and nothing could take it out. Fifteen rounds of that is
    fifteen times the cost of looking once.

    Dropping it is safe in a way dropping a tool result is not: he has already seen it, and
    the round he saw it in is where he says what it showed. That sentence stays. If he needs
    another look the file is still on disk and reading it again costs one image, not fifteen.

    The label is kept — "Here is work/page-1.jpg:" — so he can tell the difference between a
    picture he looked at and one he never opened.
    """
    keep = tuning.value("keep_images")
    seen = [i for i, m in enumerate(convo) if _carries_image(m)]
    stale = seen[:-keep] if keep > 0 else seen
    for i in stale:
        parts = convo[i].get("content")
        if not isinstance(parts, list):
            continue
        label = next(
            (p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text"),
            "An image was here.",
        )
        convo[i]["content"] = (
            f"{label} [you looked at this picture earlier; it has been set down to save room. "
            "Read the file again if you need another look.]"
        )


def _carries_image(message: dict[str, Any]) -> bool:
    parts = message.get("content")
    return isinstance(parts, list) and any(
        isinstance(part, dict) and part.get("type") == "image_url" for part in parts
    )


#: When the history is folded is not decided here any more: `history.fold_at` is the one fold
#: point for both folds, compared against the ledger's reading of the whole request. The share
#: that stood here (80% of the window) and the check beside it measured only `content` strings,
#: which missed tool-call arguments — about half of a replayed history — while the fold between
#: turns measured the same history with them.

#: How many folds a single turn will pay for before it gives up and lets shaving and
#: `_drop_oldest_exchange` take over. Counted by the turn, attempts included: a fold absorbs the
#: notes of the one before it, so a count of the notes still in the conversation never passed one
#: and never stopped anything, and a fold that failed left no note to count at all.
_MAX_FOLDS = 2


#: Rough chars-per-token, matching `llm/caching.py`, for sizing the live tool set against a
#: window before any round has calibrated a real one.
_CHARS_PER_TOKEN = 3.7


def _already_in(convo: list[dict[str, Any]], name: str, content: str) -> int | None:
    """Where this exact result already sits in the turn, or None.

    ``None`` rather than 0 for "not found", because 0 is a real index. In a live turn `convo[0]`
    is always the system prompt so a tool result can never land there, which is precisely what
    would have made the sentinel version an invisible bug: correct in production, wrong the
    moment anything built a conversation that starts with a tool result — and silently disabling
    the whole mechanism if the message order ever changed.

    Deliberately an *insert-time* question rather than a sweep over the history, and that
    distinction is the whole design. Sweeping is the obvious implementation — walk the turn,
    collapse the older copies — and it reintroduces the bug this file was just fixed for: it
    rewrites a message that has already been sent, so the prompt prefix changes and everything
    after that point re-bills uncached. On a long turn, breaking the prefix at an early message
    to save one duplicate costs far more than the duplicate did.

    Asking before appending keeps the history **append-only**, which is the property the cache
    needs. Nothing already sent is ever touched.
    """
    if len(content) < coverage.MIN_CHARS:
        return None
    for index, message in enumerate(convo):
        if message.get("role") != "tool" or message.get("_deduped"):
            continue
        if str(message.get("tool_name") or "") == name and message.get("content") == content:
            return index
    return None


def _tool_result_message(convo: list[dict[str, Any]], name: str, payload: str) -> dict[str, Any]:
    """The message to append for a tool result — the content, or a pointer to where it already is.

    Reading the same file twice puts two byte-identical copies in the window, and the second
    carries nothing the first lacks. Worth real money: in one measured session Kith read
    `ModelsSettings.tsx` fifteen times and `.kith/memory.md` thirteen, and 54% of every read he
    made was of a file he had already read.

    Unlike stubbing, this loses nothing — the content is still in the conversation, once, and
    the pointer says where. And unlike stubbing it does not tempt him to read the file *again*
    to recover it, which is how a 1,200-character stub turns into fifteen full reads.

    **Identical is the rare case.** Measured Sep 13-27, 533 reads repeated a path already read that
    turn and one was byte-identical; where a repeat does duplicate something, it is a window that
    overlaps an earlier one. So a `read_file` result that is not an exact copy is asked the finer
    question — which of its *lines* the turn already holds, unchanged — and only those are left out
    (`coverage.reduced`, which also has the measurement of how often that is). Lines he changed are
    never among them: a changed line is different text, and that difference is the record of his
    own change.

    ``_whole`` keeps what a pointer stands in for, because what it points at can be stubbed, folded
    or dropped later in the turn — and a pointer to something no longer there is worse than the
    duplicate. See `_settle_pointers`, which puts it back when that happens.
    """
    if _already_in(convo, name, payload) is not None:
        return {
            "role": "tool",
            "tool_name": name,
            "content": (
                f"[identical to what `{name}` returned earlier in this turn — that result is still "
                "above, in full, and unchanged. Nothing has been withheld and there is no need to "
                "read it again.]"
            ),
            "_deduped": True,
            "_whole": payload,
        }
    shorter = coverage.reduced(convo, name, payload)
    if shorter is not None:
        return {"role": "tool", "tool_name": name, "content": shorter, "_deduped": True, "_whole": payload}
    return {"role": "tool", "tool_name": name, "content": payload}


def _settle_pointers(convo: list[dict[str, Any]]) -> bool:
    """Make every pointer in the turn true again, after something moved. True if any changed.

    A pointer is only as good as what it points at, and three things take results away mid-turn:
    `_compact_tool_history` stubs the oldest once the live set is over budget, `compaction.fold`
    replaces the middle with a summary, `_drop_oldest_exchange` removes the oldest exchange. Each goes oldest-first, and a pointer always points *back* — so
    the thing pointed at is always the first to go, and the pointer, being small, survives it.
    Left alone it would then send him to lines that are no longer anywhere in the conversation.

    So the question each pointer answered is asked again, of the messages in front of it as they
    are now, and a pointer whose answer changed takes the new one — which is the whole result
    again when nothing above still holds its lines. The rule is one line long and needs no record
    of what pointed where: **after this runs, every pointer is exactly what `_tool_result_message`
    would write today.**

    Cache-neutral by the same argument: a pointer only changes when something *before* it did, so
    the prefix already broke at that earlier point and rewriting a later message costs nothing
    more. A stubbed pointer is left as it is — it is a stub now, and putting the whole result back
    would undo the budget that stubbed it.

    Call it after the history has been reduced and before the request is measured and sent.
    """
    changed = False
    for index, message in enumerate(convo):
        whole = message.get("_whole")
        if whole is None or message.get("_stubbed"):
            continue
        now = _tool_result_message(convo[:index], str(message.get("tool_name") or ""), whole)
        if now["content"] == message.get("content"):
            continue
        message["content"] = now["content"]
        if "_whole" not in now:
            message.pop("_whole", None)
            message.pop("_deduped", None)
        changed = True
    return changed


#: What share of the window's worth of characters may be tool output held whole.
#:
#: **Sized against what a turn actually uses, not against the window's capacity, and the
#: difference is most of the number.** A quarter of a 1M window is 971,000 characters — about
#: 262,000 tokens — and the p90 turn on this install runs 400,000 tokens per round in total, so
#: a quarter would have been generous enough never to bind on the very turns it exists for. The
#: window says what the model may be *billed* for; it says nothing about how much old shell
#: output can sit in front of a question before the question stops being the loudest thing in
#: the prompt.
#:
#: Twelve per cent is ~466,000 characters on a million-token model: roughly fifty nine-thousand
#: character reads held whole before anything is let go, and **5.8x the old absolute budget**,
#: which is the margin that matters — 80,000 characters is what caused the re-reading, and
#: nothing here is trimmed until nearly six times that much is live.
#:
#: **A share rather than a constant, and that is the whole fix.** `live_tool_chars` is 80,000,
#: which was right for the model this loop was written against and is 1.9% of a million-token
#: window. Trimming to it made Kith re-read `ModelsSettings.tsx` fifteen times — 54% of every
#: read he made was of a file he had already read — so in September compaction was gated behind
#: "actually short of room" and stopped running at all. That left two states and no middle:
#: below 80% of the window nothing is trimmed ever, above it the live set is slammed to 1.9%.
#: Measured across 478 real turns, only the first state ever occurs; the median turn is 16
#: rounds and the p90 is 83, and none of them reach 838,860 tokens in a single round. So the
#: sixty rounds of shell output are all still there, and the machinery for that has never run.
_LIVE_TOOL_SHARE = 0.12


def live_tool_budget(window: int) -> int:
    """How many characters of tool output may stay whole, for this model.

    ``window`` is the model's context in tokens, or 0 when nobody knows — load-bearing here
    exactly as in `_room_is_tight` and `config.context_window`. An unknown window falls back to
    the absolute setting, because guessing is worse in both directions: guess high and a small
    model 400s mid-turn, guess low and every turn is trimmed for nothing.
    """
    absolute = tuning.value("live_tool_chars")
    if window <= 0:
        return absolute
    return int(window * _CHARS_PER_TOKEN * _LIVE_TOOL_SHARE)


def _compact_tool_history(convo: list[dict[str, Any]], offload=None, window: int = 0) -> None:
    """Stub the oldest tool outputs once the live set outgrows its budget.

    Tool output re-sends in full on every subsequent round, so a long research turn
    would balloon without pruning. The pruning has to be sized to the model actually
    in use: a hard "keep the last four" is ruinous on a 1M-context model, where he
    forgets the six searches he ran two rounds ago and re-runs them, round after round.

    So the live set is bounded by characters rather than by count — keep the newest
    results whole until the budget is spent, and only then start stubbing. The budget
    now follows the window (see `live_tool_budget`); the rest are settings, read here
    rather than at import so raising them takes effect on the next turn.

    **Cache safety is a property of the budget being big enough**, not of this never running.
    Below the budget nothing is rewritten at all, so round N+1 still contains round N as a
    byte-identical prefix and the breakpoint written last round is read this round. Above it
    the eviction frontier advances and the prefix does break there — that is the real cost of
    trimming, paid only by turns that have genuinely run away, which are the turns where
    carrying everything costs more than re-billing the tail.

    ``offload(name, content) -> path`` spills an evicted result to a file first, so the
    stub can point him at the full text rather than truncating it away. Optional and
    defaulted off: without it — a caller with no conversation to file under, or a test —
    the stub is the older, lossy trim, unchanged.
    """
    keep_whole = tuning.value("keep_full_tool_results")
    char_budget = live_tool_budget(window)
    stub_chars = tuning.value("tool_stub_chars")
    tool_positions = [i for i, m in enumerate(convo) if m.get("role") == "tool"]

    # Walk newest-first, spending the budget on the most recent results.
    keep: set[int] = set()
    spent = 0
    for rank, i in enumerate(reversed(tool_positions)):
        size = len(convo[i].get("content") or "")
        if rank < keep_whole or spent + size <= char_budget:
            keep.add(i)
            spent += size
        else:
            break  # everything older than the first eviction goes too

    for i in tool_positions:
        if i in keep:
            continue
        msg = convo[i]
        content = msg.get("content") or ""
        if len(content) > stub_chars and not msg.get("_stubbed"):
            name = msg.get("tool_name", "tool")
            saved = offload(name, content) if offload else ""
            if saved:
                # The tail is kept on disk, one read_file away, instead of thrown out.
                msg["content"] = (
                    content[:stub_chars] + f"\n…[the full {len(content):,}-character {name} output was "
                    f"moved to {saved} to save room — read_file it if you still need the rest]"
                )
            else:
                msg["content"] = (
                    content[:stub_chars] + f"\n…[earlier {name} output trimmed to save room — "
                    "if you still need it, save what matters to a file next time; re-run the tool to see it again]"
                )
            msg["_stubbed"] = True
