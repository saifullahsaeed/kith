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

* the system prompt — it is the cached head;
* the first thing his person said — losing the actual request is the one unrecoverable loss,
  and a summary of it is exactly the paraphrase that flattened "discover models through the
  OpenRouter API" into "add model management" on a real project;
* the last `keep_last` messages — the work in progress, where the detail is still live.

The middle becomes one message. Everything about it is fixed at the moment it is written, so
it never changes again.
"""

from __future__ import annotations

from typing import Any

#: How many messages at the end of the turn survive untouched. Generous on purpose: these are
#: the rounds he is actually working in, and a fold that reaches into them replaces detail he
#: is still using with prose about it.
KEEP_LAST = 14

#: Below this there is nothing worth folding — the call would cost more than the space it wins.
MIN_TO_FOLD = 8

_INSTRUCTION = """You are compacting your own working notes so you can keep going in a smaller space.

Below is the middle stretch of a turn you are part-way through. Rewrite it as a handover to
yourself. You will lose the originals, so anything not written down is gone.

Cover, in this order, and only what is actually there:
1. WHAT WAS ASKED — the goal in the requester's terms, not yours.
2. WHAT IS DONE — decisions made, files created or changed (exact paths), commands that worked.
3. WHAT IS TRUE — facts you established about the system: real names, real signatures, the
   actual database or framework in use. Quote the specifics; a claim you cannot support is
   worse than a gap.
4. DEAD ENDS — what you already tried that did not work, and why. This is the most valuable
   part: without it you will try it again.
5. WHAT IS LEFT — the next concrete step.

Be dense and specific. Paths, names, numbers, error messages. No preamble, no reassurance, no
"I will continue" — this is a note to yourself, not a status report to anyone."""

_HEADER = "[Earlier steps of this turn, folded into notes to make room:]\n\n"

#: Written when the model cannot be reached. Byte-stable — no counts, no sizes — because a note
#: that reads "9 steps" one round and "11 steps" the next invalidates the prefix from that point
#: on every round, which is the bug this whole module exists to avoid.
_FAILED = (
    "[Earlier steps of this turn were folded to make room, and the summary could not be "
    "written. Their results are gone from this conversation. Re-read anything you still need.]"
)


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
    """How many messages at the front are kept as-is: the system prompt and the first ask."""
    head = 1 if convo and convo[0].get("role") == "system" else 0
    if head < len(convo) and convo[head].get("role") == "user":
        head += 1
    return head


def _as_text(convo: list[dict[str, Any]]) -> str:
    lines = []
    for message in convo:
        role = str(message.get("role") or "")
        content = message.get("content")
        if isinstance(content, list):
            content = " ".join(
                str(part.get("text") or "[picture]")
                for part in content
                if isinstance(part, dict)
            )
        text = str(content or "")
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            text += f"\n[called {function.get('name')} {str(function.get('arguments') or '')[:400]}]"
        if message.get("role") == "tool":
            # `tool_name` is the key the agent loop tags results with; `name` is what a
            # provider-shaped message would use. Accept either, so this reads a live
            # conversation and a hand-built test one the same way.
            role = f"result of {message.get('tool_name') or message.get('name') or 'a tool'}"
        if text.strip():
            lines.append(f"--- {role}:\n{text}")
    return "\n\n".join(lines)


def fold(
    convo: list[dict[str, Any]],
    ask,
    keep_last: int = KEEP_LAST,
) -> bool:
    """Replace the middle of `convo` in place with a summary of it. True if anything changed.

    ``ask(prompt) -> str`` makes the one model call, and is injected rather than imported so
    this is testable without a provider and so the caller decides which model and host to spend.
    A caller that cannot summarise should still be able to fold — see `_FAILED`.
    """
    if len(convo) < MIN_TO_FOLD:
        return False
    head = _keep_head(convo)
    end = cut_point(convo, keep_last)
    if end <= head:
        return False

    middle = convo[head:end]
    if not middle:
        return False

    try:
        notes = str(ask(f"{_INSTRUCTION}\n\n{_as_text(middle)}") or "").strip()
    except Exception:
        notes = ""

    folded = {"role": "user", "content": (_HEADER + notes) if notes else _FAILED, "_folded": True}
    convo[head:end] = [folded]
    return True


def already_folded(convo: list[dict[str, Any]]) -> int:
    """How many folds this turn has already done.

    A very long turn can fold more than once, and a later fold deliberately takes the earlier
    summary into its middle rather than preserving it — the alternative is a chain of summaries
    with a little more distance from the work in each, and one dense note beats three vague ones.

    The count is the guard on that: summarising a summary loses something every time, so past a
    couple of passes there is nothing left worth another model call and the caller should let
    the hard drop take over. Without this a turn that outgrows the window faster than folding
    shrinks it would fold every round forever, paying for a summary each time.
    """
    return sum(1 for message in convo if message.get("_folded"))
