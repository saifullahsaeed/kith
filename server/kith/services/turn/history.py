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

#: Arguments that can be an entire file. Worth everything while the call is being made and
#: nothing once it has run — the result already says whether it worked.
_BULKY_ARGS = ("content", "new", "old")


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
            trimmed = False
            for key in _BULKY_ARGS:
                value = args.get(key)
                if isinstance(value, str) and len(value) > stub_chars:
                    args[key] = f"…[{len(value):,} characters, sent earlier in this turn]"
                    trimmed = True
            if trimmed:
                function["arguments"] = json.dumps(args)


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


#: Share of the model's context that may fill before the history is folded into a summary.
#:
#: Not near the brim, because `ContextBudget` is the overflow guard and this is not — and a
#: valve that only opens at the last moment opens during the rounds that can least afford to
#: spend one on a summary. Not near the middle either: every round below this threshold is a
#: round where the whole history re-sends at cache-read price, which is the cheap outcome.
_FOLD_ABOVE_SHARE = 0.8


#: How many folds a single turn will pay for before it gives up and lets `_drop_oldest_exchange`
#: take over. Each fold costs a model call and summarises the previous summary, so the third one
#: is buying very little with real money.
_MAX_FOLDS = 2


#: Rough chars-per-token, matching `llm/caching.py`. Only used for the no-window fallback below,
#: where being a little pessimistic is the safe direction.
_CHARS_PER_TOKEN = 3.7


#: What the old char-budget fallback compares against when the window is unknown. Same figure
#: the shaving path has always used, so an install with no known window behaves exactly as
#: before rather than inheriting a threshold derived from a window nobody can name.
_UNKNOWN_WINDOW_CHARS = 80_000


def _room_is_tight(convo: list[dict[str, Any]], window: int) -> bool:
    """Should this conversation's history be reduced before the next request?

    ``window`` is the model's context in tokens, or 0 when nobody knows — and 0 is
    load-bearing here exactly as it is in `config.context_window`. An unknown window means we
    cannot say what fraction of it we are using, so the honest answer is to fall back to an
    absolute character budget and behave as this loop always did. Guessing a window would be
    worse in both directions: guess high and a small model 400s mid-turn, guess low and every
    turn is reduced for nothing.
    """
    held = sum(len(str(message.get("content") or "")) for message in convo)
    if window <= 0:
        return held > _UNKNOWN_WINDOW_CHARS
    return held > window * _CHARS_PER_TOKEN * _FOLD_ABOVE_SHARE


#: Below this a duplicate is not worth replacing — the pointer is not free either, and a short
#: result carries its own answer.
_DEDUPE_MIN_CHARS = 400


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
    if len(content) < _DEDUPE_MIN_CHARS:
        return None
    for index, message in enumerate(convo):
        if message.get("role") != "tool" or message.get("_deduped"):
            continue
        if str(message.get("tool_name") or "") == name and message.get("content") == content:
            return index
    return None


def _tool_result_message(convo: list[dict[str, Any]], name: str, payload: str) -> dict[str, Any]:
    """The message to append for a tool result — the content, or a pointer to an identical one.

    Reading the same file twice puts two byte-identical copies in the window, and the second
    carries nothing the first lacks. Worth real money: in one measured session Kith read
    `ModelsSettings.tsx` fifteen times and `.kith/memory.md` thirteen, and 54% of every read he
    made was of a file he had already read.

    Unlike stubbing, this loses nothing — the content is still in the conversation, once, and
    the pointer says where. And unlike stubbing it does not tempt him to read the file *again*
    to recover it, which is how a 1,200-character stub turns into fifteen full reads.

    Two reads of a file he edited in between are not identical, so both survive. That difference
    is the record of his own change and is the last thing to collapse.
    """
    if _already_in(convo, name, payload) is None:
        return {"role": "tool", "tool_name": name, "content": payload}
    return {
        "role": "tool",
        "tool_name": name,
        "content": (
            f"[identical to what `{name}` returned earlier in this turn — that result is still "
            "above, in full, and unchanged. Nothing has been withheld and there is no need to "
            "read it again.]"
        ),
        "_deduped": True,
    }


def _compact_tool_history(convo: list[dict[str, Any]], offload=None) -> None:
    """Stub the oldest tool outputs once the live set outgrows its char budget.

    Tool output re-sends in full on every subsequent round, so a long research turn
    would balloon without pruning. The pruning has to be sized to the model actually
    in use: a hard "keep the last four" is ruinous on a 1M-context model, where he
    forgets the six searches he ran two rounds ago and re-runs them, round after round.

    So the live set is bounded by characters rather than by count — keep the newest
    results whole until the budget is spent, and only then start stubbing. All three
    numbers are settings, read here rather than at import so raising them for a
    bigger model takes effect on the next turn instead of the next restart.

    ``offload(name, content) -> path`` spills an evicted result to a file first, so the
    stub can point him at the full text rather than truncating it away. Optional and
    defaulted off: without it — a caller with no conversation to file under, or a test —
    the stub is the older, lossy trim, unchanged.
    """
    keep_whole = tuning.value("keep_full_tool_results")
    char_budget = tuning.value("live_tool_chars")
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
