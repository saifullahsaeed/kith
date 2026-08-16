"""Saying something to a turn that is already running.

Until now there was one way to change your mind mid-turn: Stop. That kills the run and throws
away whatever it had worked out, and then the next message starts from a cold prompt. It is a
fire alarm, and what people reach for it with is a steering wheel.

Watched in a real conversation: a question arrived while the previous work was still the most
recent thing in the prompt, and the turn carried straight on with the old task — because there
was no way for the new words to reach a loop that had already begun.

**Two ways to send, and the difference is when it arrives.**

* *Steer* — the default. The text lands at the next round boundary, inside the turn that is
  already running. Nothing is thrown away: every tool result and every conclusion so far stays
  in the prompt, and the new instruction arrives as the freshest thing in it.
* *Queue* — held until the turn finishes, then sent as an ordinary new message. For when the
  point is "when you're done, do this next" rather than "actually, do this instead".

**A round boundary, not an interrupt.** The loop is checked between rounds rather than being
signalled mid-flight, because a round is the only moment the conversation is a consistent list:
inside one there is a half-read stream, a tool call that has been announced but not answered,
and appending to that produces a message list no provider will accept. The wait is one round —
seconds — and it costs nothing that a torn prompt would not cost more.

Per conversation, in memory, and deliberately not in the database. A steer is only meaningful
to a turn that is running right now; one that outlived a restart would arrive in a conversation
that had forgotten what it was about.
"""

from __future__ import annotations

import threading

#: Conversation id to the text waiting to go into its running turn.
_PENDING: dict[str, list[str]] = {}

#: One lock over the dict. Written from Flask request threads (someone typing) and read from
#: turn threads (the loop, between rounds), which is two thread pools on one piece of state —
#: the shape `infra/permissions.py` already had to grow a lock for.
_LOCK = threading.RLock()

#: Most steers held for one turn. Past a handful the person is not steering, they are typing at
#: a wall, and delivering nine of them at once would bury the round they were meant to change.
MAX_WAITING = 5


def steer(conversation_id: str, text: str) -> bool:
    """Leave text for the turn running in this conversation. False if there was nothing to say.

    Does not check whether a turn is running. The caller knows — it has just asked
    `live_turns.current` — and a race between that check and this call resolves the right way
    either way: text left for a turn that has just finished is taken by `pending_for` on the
    next one, or dropped by `forget` when the conversation moves on.
    """
    said = (text or "").strip()
    if not said:
        return False
    with _LOCK:
        waiting = _PENDING.setdefault(conversation_id, [])
        if len(waiting) >= MAX_WAITING:
            return False
        waiting.append(said)
    return True


def waiting(conversation_id: str) -> int:
    """How many steers are queued. For telling someone their words have not been lost."""
    with _LOCK:
        return len(_PENDING.get(conversation_id) or [])


def take(conversation_id: str) -> str:
    """Everything waiting, as one message, cleared as it is read.

    Joined rather than delivered one per round: two sentences typed three seconds apart are one
    thought, and splitting them across rounds means the model acts on half of it first.
    """
    with _LOCK:
        said = _PENDING.pop(conversation_id, [])
    return "\n\n".join(said)


def forget(conversation_id: str) -> None:
    """Drop anything waiting. Called when a turn ends, so nothing arrives in the next one.

    Text left over is text nobody has answered — but it belonged to a turn that is now over,
    and carrying it forward would put it in a prompt whose recent history no longer matches
    what it was reacting to.
    """
    with _LOCK:
        _PENDING.pop(conversation_id, None)


def forget_everything() -> None:
    """For tests, and for a server shutting down."""
    with _LOCK:
        _PENDING.clear()
