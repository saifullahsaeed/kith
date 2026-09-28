"""Fold the middle of a long turn into a summary, once, instead of shaving it every round.

The difference between this and `_compact_tool_history` is shape, not degree, and the shape is
what matters:

* **Shaving** rewrites a little more of the history on every round, and the point it rewrites
  moves forward as the conversation grows. So the prompt prefix changes every round, forever,
  and the cache breakpoint on the last message can never be read. Measured: 7% of a turn
  byte-identical between consecutive rounds, 93% re-billed uncached — by the mechanism that
  existed to stop it being re-billed.
* **Folding** happens once, at a threshold, and produces a *new stable prefix*. The round after
  a fold is byte-identical to the fold, so caching resumes immediately and stays working for
  the rest of the turn. It costs one model call, and buys back every round after it.

What is kept verbatim, and why each one:

* **everything the turn was handed** — the system prompt, and in a conversation the folded
  brief, the recent turns, the message being answered and the block about the present. It is
  the cached head, and it holds the request: losing the actual request is the one unrecoverable
  loss, and a summary of it is exactly the paraphrase that flattened "discover models through
  the OpenRouter API" into "add model management" on a real project. This used to keep "the
  system prompt and the first user message", which in a conversation is the first thing ever
  said in it — so the message the turn was answering, the brief and the verbatim questions were
  all folded into prose about them;
* the last `keep_last` messages — the work in progress, where the detail is still live.

The middle becomes one message. Everything about it is fixed at the moment it is written, so
it never changes again. The request that writes it is the turn's own prompt up to the cut, with
the instruction appended — see `services.brief` for why that, and not a transcript of the middle.
"""

from __future__ import annotations

from typing import Any

from kith.services import brief

#: How many messages at the end of the turn survive untouched. Generous on purpose: these are
#: the rounds he is actually working in, and a fold that reaches into them replaces detail he
#: is still using with prose about it.
KEEP_LAST = 14

#: Below this there is nothing worth folding — the call would cost more than the space it wins.
MIN_TO_FOLD = 8

_HEADER = "[Earlier steps of this turn, folded into notes to make room:]\n\n"

#: What the notes stand in for, as the instruction's subject. Not "the conversation": the turn's
#: head — its request, and in a conversation the history before it — is kept, and a brief that
#: re-told it would pay twice for it.
_REPLACES = "Everything this turn did above, after the message it is answering,"


def _open_calls_at(convo: list[dict[str, Any]]) -> list[int]:
    """For each index, how many tool calls are still awaiting their result.

    A cut is only safe where this is zero. An assistant message carrying `tool_calls` must be
    followed by a `tool` message per call: split them and the provider rejects the whole
    request, which would turn a routine fold into a dead turn.
    """
    open_now = 0
    depth = []
    for message in convo:
        depth.append(open_now)
        if message.get("role") == "assistant" and message.get("tool_calls"):
            open_now += len(message.get("tool_calls") or [])
        elif message.get("role") == "tool":
            open_now = max(0, open_now - 1)
    depth.append(open_now)
    return depth


def cut_point(convo: list[dict[str, Any]], keep_last: int = KEEP_LAST) -> int:
    """Where the folded region ends — the largest safe boundary that still keeps `keep_last`.

    Returns 0 when there is nothing safe to fold, which the caller must treat as "leave it
    alone" rather than "fold everything".
    """
    depth = _open_calls_at(convo)
    wanted = len(convo) - keep_last
    for index in range(min(wanted, len(convo) - 1), 0, -1):
        if depth[index] == 0 and convo[index].get("role") != "tool":
            return index
    return 0


def _keep_head(convo: list[dict[str, Any]]) -> int:
    """How many messages at the front are kept as-is: everything the turn was handed.

    In a conversation that ends at the present-state block (`_live`), which the prompt builder
    always puts last, straight after the message being answered. A turn with no such block — a
    builder, a sub-agent, a test — was handed a system prompt and one request, and keeps both.
    """
    live = [index for index, message in enumerate(convo) if message.get("_live")]
    if live:
        return live[-1] + 1
    head = 1 if convo and convo[0].get("role") == "system" else 0
    if head < len(convo) and convo[head].get("role") == "user":
        head += 1
    return head


def can_fold(convo: list[dict[str, Any]], keep_last: int = KEEP_LAST) -> bool:
    """Is there a middle to fold? Asked before announcing one, so the person is not told a turn
    is folding when there was nothing to fold."""
    return len(convo) >= MIN_TO_FOLD and cut_point(convo, keep_last) > _keep_head(convo)


def _earlier_notes(middle: list[dict[str, Any]]) -> str:
    """The text of an earlier fold's notes inside the region about to be folded, or ""."""
    for message in middle:
        if message.get("_folded"):
            return str(message.get("content") or "").removeprefix(_HEADER)
    return ""


def fold(
    convo: list[dict[str, Any]],
    ask,
    keep_last: int = KEEP_LAST,
) -> bool:
    """Replace the middle of `convo` in place with a summary of it. True if anything changed.

    ``ask(messages) -> str`` makes the one model call and returns the notes, or "" when what came
    back is not usable. It is handed the turn's own messages up to the cut with the instruction
    appended, and is injected rather than imported so this is testable without a provider and so
    the caller decides which model, tools and host to spend — which must be the turn's own, or
    the request is not the prompt the host already holds.

    **A failed summary leaves the turn exactly as it was.** This used to replace the middle with
    a note saying the summary could not be written, which was right when the fold fired at 80% of
    the window and the room was needed whatever happened. It fires at the fold point now, far
    below that on a large model, where it is about cost rather than room — and a turn that has
    lost the results it was working from re-reads them, which costs more than the fold saved.
    When room genuinely runs out, `_drop_oldest_exchange` is what makes it.

    A later fold in the same turn keeps the earlier notes' lasting sections as they are and asks
    only for what is new (`brief.carried`): summarising the previous summary is how the start of a
    long turn decays to nothing.
    """
    if not can_fold(convo, keep_last):
        return False
    head = _keep_head(convo)
    end = cut_point(convo, keep_last)

    earlier = _earlier_notes(convo[head:end])
    continuing = brief.is_a_brief(earlier)
    request = [
        *convo[:end],
        {
            "role": "user",
            "content": brief.instruction(
                _REPLACES, earlier=_HEADER.strip() if earlier else "", continuing=continuing
            ),
        },
    ]
    try:
        notes = str(ask(request) or "").strip()
    except Exception:
        notes = ""
    if not notes:
        return False
    if continuing:
        notes = brief.carried(earlier, notes)

    convo[head:end] = [{"role": "user", "content": _HEADER + notes, "_folded": True}]
    return True
