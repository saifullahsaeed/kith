"""The turn that is running, kept so it can be watched more than once.

A turn already survives you leaving. It runs on its own thread, and hanging up the request
stopped ending it the day stopping became something you say out loud rather than something
inferred from a closed socket. What did *not* survive was everything it said while you were
gone: the lines went into a `queue.Queue` created inside the request handler, and when that
request ended there was nobody draining it and nowhere to look. Switch conversations mid-answer
and the work carries on, invisibly, until it finishes and the whole reply appears at once —
which reads as the turn having been killed and restarted, and is the single most disconcerting
thing the interface does.

So the turn's output lives here instead of in the request. One record per conversation, holding
everything emitted so far and the set of readers currently following. A reader gets the backlog
first and then the rest as it happens, which means "attach" and "start" are the same operation
seen at different times — the first reader is the request that began the turn, and it takes the
identical path as one that arrives forty seconds late.

Kept in memory rather than on disk on purpose. This is only ever the *current* turn: once it
ends, the transcript is the record and it is authoritative, so the buffer is dropped and a
returning reader is told there is nothing live rather than being handed a stale replay.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field

from kith.kernel import changes

#: Pushed to a watcher's queue to mean "the turn is over, stop reading".
_END = object()


@dataclass
class LiveTurn:
    conversation_id: str
    #: Every line emitted so far, in order. What a late reader is caught up with.
    lines: list[str] = field(default_factory=list)
    done: bool = False
    watchers: set[queue.Queue] = field(default_factory=set)
    #: Held across "append to the backlog, then fan out". Without it a watcher registering
    #: between those two steps gets a backlog missing the line that is being delivered, which
    #: is a dropped token rather than a duplicated one — much harder to notice.
    lock: threading.Lock = field(default_factory=threading.Lock)


_LIVE: dict[str, LiveTurn] = {}
_REGISTRY = threading.Lock()


def begin(conversation_id: str) -> LiveTurn:
    """Open the record for a turn about to start, replacing any stale one.

    And say so on the changes stream, which is what lets an open window attach to a turn *it* did not
    start — a reminder firing, a background task finishing. Without this the transcript grew on disk
    and the window found out when somebody reloaded it.
    """
    turn = LiveTurn(conversation_id=conversation_id)
    with _REGISTRY:
        _LIVE[conversation_id] = turn
    _announce(conversation_id)
    return turn


def current(conversation_id: str) -> LiveTurn | None:
    """The turn running in this conversation, if one is."""
    with _REGISTRY:
        turn = _LIVE.get(conversation_id)
    return None if turn is None or turn.done else turn


def live() -> list[str]:
    """Which conversations have a turn running right now.

    The interface asks because it has nothing else to ask. A turn is live in memory here and
    nowhere else — the transcript only shows it once it is over — so "is he working, and where" is
    a question only this module can answer, and the app spent a long time not asking: the presence
    orb and the ambient wash were hardcoded off, with a comment saying they were waiting for one
    honest meaning of busy. This is that meaning: a session is busy if and only if a turn is live
    in it.

    A list of ids rather than a count, because "something is running" and "*that* conversation is
    running" are different sentences and the second is the useful one — it is what lets the header
    say which chat to go back to.
    """
    with _REGISTRY:
        return [id for id, turn in _LIVE.items() if not turn.done]


def publish(turn: LiveTurn, line: str) -> None:
    """Record one line and hand it to everyone watching."""
    with turn.lock:
        turn.lines.append(line)
        watchers = list(turn.watchers)
    for watcher in watchers:
        watcher.put(line)


def _announce(conversation_id: str) -> None:
    """Local import and swallowed: `live_turns` is imported by the chat route on every turn, and a
    notification must not be able to stop one starting."""
    try:
        changes.publish("turn", conversation_id)
    except Exception:
        pass


def finish(turn: LiveTurn) -> None:
    """The turn is over. Release the readers and drop the backlog.

    Identity-checked before removal, for the same reason `_disarm` in the chat route is: a
    conversation outlives the turn running in it, so a finishing turn must not delete a record
    belonging to one that started after it.
    """
    with turn.lock:
        turn.done = True
        watchers = list(turn.watchers)
        turn.lines.clear()
    for watcher in watchers:
        watcher.put(_END)
    _announce(turn.conversation_id)
    with _REGISTRY:
        if _LIVE.get(turn.conversation_id) is turn:
            del _LIVE[turn.conversation_id]


def watch(turn: LiveTurn) -> Iterator[str]:
    """Everything this turn has said, then everything it says next.

    The registration and the backlog snapshot happen under one lock, so a line published
    while a reader is attaching lands in exactly one of the two — never neither.
    """
    inbox: queue.Queue = queue.Queue()
    with turn.lock:
        if turn.done:
            return
        backlog = list(turn.lines)
        turn.watchers.add(inbox)
    try:
        yield from backlog
        while True:
            item = inbox.get()
            if item is _END:
                return
            yield item
    finally:
        # A reader that hangs up — closed the tab, switched away again — must not leave a queue
        # behind that `publish` goes on filling for the rest of the turn.
        with turn.lock:
            turn.watchers.discard(inbox)
