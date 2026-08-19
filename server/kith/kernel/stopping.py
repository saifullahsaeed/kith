"""Whether the turn running in a conversation has been asked to stop.

One `threading.Event` per conversation, armed when a turn begins and cleared when it ends.
The chat route owns *deciding* to stop — that means setting this and then releasing whatever
the turn is parked on, a question or a permission, which are services and infra and have no
business in here. What lives here is only the switch itself.

**In the kernel because everything has to be able to read it and nothing can be handed it.**
It began as four private functions and a dict inside `api/routes/chat.py`, which was right
while the only reader was the loop the route was driving: the route held the event as a local
and checked it between events. Then a tool needed to read it, and a tool cannot import a route
— `kith.tools` is an adapter and so is `kith.api`, and the route imports the tool layer, so the
edge would be a cycle as well as an inversion.

The tool that needed it is `delegate_subtask`. A sub-agent runs a whole second loop inside one
tool call, and the parent loop emits nothing at all while it does, so the route's between-events
check cannot fire — Stop did nothing for the length of an errand. That is the same shape of
problem `session_context` and `live_turns` are here for: a fact about the running turn that code
fifty frames down needs and cannot be passed.

Per conversation, not per process. Two chats streaming at once are two turns, and stopping one
must not touch the other.
"""

from __future__ import annotations

import threading

#: Conversation id to the switch of the turn currently running in it.
#:
#: Keyed by conversation and *identified by the event object*, not by the key — see
#: :func:`disarm` for the bug that reads as "Stop says there is nothing running".
_RUNNING: dict[str, threading.Event] = {}

#: Held across read-then-write on `_RUNNING`. The turns contending for it are on separate
#: threads by design, so "check whether this is still mine, then remove it" has to be one step.
_LOCK = threading.Lock()


def arm(conversation_id: str) -> threading.Event:
    """The switch for a turn about to start, and the one it must read for the rest of its life.

    Returned rather than looked up again later. A turn that re-reads `_RUNNING[id]` between
    events is reading whichever turn started most recently, which is how one click stopped two.
    """
    event = threading.Event()
    with _LOCK:
        _RUNNING[conversation_id] = event
    return event


def disarm(conversation_id: str, event: threading.Event) -> None:
    """Forget a finished turn's switch — but only if it is still the current one.

    The `if` is the whole point. An unconditional `pop` meant the first turn to *finish*
    deleted the entry a still-running turn was registered under, and Stop then answered
    `{"stopping": false}` for a turn visibly in progress.
    """
    with _LOCK:
        if _RUNNING.get(conversation_id) is event:
            del _RUNNING[conversation_id]


def current(conversation_id: str) -> threading.Event | None:
    """The switch of the turn running in this conversation, if one is."""
    with _LOCK:
        return _RUNNING.get(conversation_id)


def stop(conversation_id: str) -> bool:
    """Set the switch. False if no turn was running.

    Only the flag. Waking whatever the turn is parked on — an unanswered question, a permission
    prompt — is the caller's, and the order matters: see `api/routes/chat._stop`, which does
    this first and releases after, because releasing first hands the turn its next round before
    it has been told to stop.
    """
    event = current(conversation_id)
    if event is not None:
        event.set()
    return event is not None


def asked_to_stop(conversation_id: str) -> bool:
    """Has the turn in this conversation been asked to stop?

    The read for code that is *inside* a turn rather than driving one — a sub-agent between its
    rounds, a long tool between its steps. Answers False for a conversation with no turn and for
    no conversation at all, which is the honest answer in a test or a script: nothing has been
    asked to stop, because nothing was asked to start.
    """
    if not conversation_id:
        return False
    event = current(conversation_id)
    return event is not None and event.is_set()
