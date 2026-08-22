"""One event log for the process, and the only thing the interface listens to.

There were two push channels and they failed in opposite directions, for the same reason.

`/api/changes` fanned out to a set of queues and threw each one away on disconnect, so a laptop
waking up, a backend restart or a dropped connection lost everything queued and the window went
quietly stale. `/api/activity/stream` did the reverse: it re-sent the whole of `feed.recent()` to
every new connection, with nothing on the wire identifying a line and no dedupe on the client — so
a reconnect duplicated the last hundred lines into the feed.

Loss on one, duplicates on the other, and one missing mechanism under both: **the events had no
identity.** Which is also why the interface still polled. Eleven `setInterval`s were not laziness;
they were the safety net under a channel that could silently drop, and no amount of them fixes a
feed that repeats itself. A channel you can resume is what lets the timers go.

So: a monotonic id on every event, a bounded log of recent ones, and a subscriber that can say
where it got to. That is SSE's own `id:`/`Last-Event-ID` protocol, which is the standard answer to
exactly this and which neither stream was using.

Three ways a cursor can be wrong, and each has an honest reply:

* **behind, but still in the log** — replay the tail. The client missed nothing.
* **older than the log** — say `resync`. There is no honest replay, so the client refetches
  everything rather than being told it is up to date when it is not.
* **from another run** — the process restarted underneath it and the numbering began again. Also
  `resync`: the server was down, so the world may have moved with no event published at all.

That last case is why an id is not just a number. Comparing numbers alone catches a restart only
while the new run is still *behind* the old cursor: reconnect after the fresh process has published
past it and 500 looks like an ordinary position, so the client is handed events 501 onward from a
completely different sequence and told it is caught up. So every id carries the run it belongs to —
`<epoch>-<n>` on the wire, which SSE allows because it treats the id as opaque text — and a cursor
from another epoch is not a cursor at all.

A subscriber whose queue overflows is not a fourth case. It drops its oldest and takes the newest,
because blocking a task being saved to protect a notification about it has the priority backwards —
and the client sees a gap in the ids and resyncs itself. Every loss lands in one of the cases above,
which is the property that lets the interface trust this.

The log is per-process and in memory. It is a recovery window for a reconnect, not a record: the
transcript, the board and the database are where things are kept.
"""

from __future__ import annotations

import queue
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field

#: What can be on the wire. `changed` says a kind of thing moved and carries no values; `activity`
#: carries a feed line, which *is* the value and has nowhere else to be read from.
TYPES = ("changed", "activity", "resync")

#: This run of the process. Regenerated on every start, which is the point: it is what tells a
#: reconnecting client that the numbers it remembers belong to a sequence that no longer exists.
EPOCH = uuid.uuid4().hex[:8]

#: How many events the log holds, and the ceiling on one subscriber's queue.
#:
#: The number that matters is "long enough to cover a reconnect". A dropped connection is retried
#: within seconds and a machine waking from sleep within one; five hundred events covers minutes of
#: a busy turn, and past that a `resync` is both cheaper and more honest than a longer replay.
BACKLOG = 500


@dataclass(frozen=True)
class Event:
    """One thing that happened, with an identity.

    The id is what the whole design turns on: it is what a client sends back as `Last-Event-ID`,
    what lets it notice a gap, and what makes a duplicate recognisable as one.
    """

    id: int
    type: str
    data: dict


@dataclass
class Subscription:
    """A place in the log, and what the subscriber missed getting to it."""

    queue: queue.Queue
    #: Events published after the client's cursor and still in the log. Sent before anything new.
    missed: list[Event] = field(default_factory=list)
    #: The cursor could not be honoured. The client must refetch everything it holds.
    resync: bool = False
    #: The newest id at the moment of joining, so a `resync` can carry a cursor that is current
    #: rather than leaving the client to guess one.
    at: int = 0


class Events:
    """The log: monotonic ids, a bounded history, and a set of subscriber queues."""

    def __init__(self, backlog: int = BACKLOG) -> None:
        self._lock = threading.Lock()
        self._subscribers: set[queue.Queue] = set()
        self._log: deque[Event] = deque(maxlen=backlog)
        self._next = 1

    # -- writing ------------------------------------------------------------ #

    def publish(self, type: str, data: dict) -> Event:
        """Record an event and hand it to everyone listening.

        Called from every write path there is, so it cannot block and cannot raise. Taking the id
        and appending to the log happen under the lock — two events published from two threads must
        not be able to agree on a number — and the queues are filled outside it, because a full
        queue's drop-and-retry is not something a writer should be holding a lock through.
        """
        with self._lock:
            event = Event(id=self._next, type=type, data=data)
            self._next += 1
            self._log.append(event)
            subscribers = list(self._subscribers)

        for subscription in subscribers:
            try:
                subscription.put_nowait(event)
            except queue.Full:
                # Drop the oldest and take the newest. Losing one is recoverable — the client
                # sees id 7 after id 5 and resyncs — and blocking the save that caused it is not.
                try:
                    subscription.get_nowait()
                    subscription.put_nowait(event)
                except Exception:
                    pass
        return event

    # -- reading ------------------------------------------------------------ #

    def subscribe(self, since: int | None = None, epoch: str | None = None) -> Subscription:
        """Join the log, optionally from a cursor.

        Joining and reading the backlog happen under **one** lock, and that is the point of doing
        it here rather than in the route. They are two moments otherwise: read-then-join loses an
        event published in between, and join-then-read delivers it twice. Both are the bugs this
        module exists to end, so neither is allowed back in through the door.
        """
        subscription: queue.Queue = queue.Queue(maxsize=self._log.maxlen or BACKLOG)
        with self._lock:
            self._subscribers.add(subscription)
            newest = self._log[-1].id if self._log else 0
            oldest = self._log[0].id if self._log else self._next

            if since is None:
                # A fresh client. It fetches its own state through the API it already uses and
                # only needs what happens from now on — replaying a backlog it never saw is how
                # the activity feed came to duplicate itself.
                return Subscription(queue=subscription, at=newest)

            if epoch is not None and epoch != EPOCH:
                # A cursor from another run of this process. Its numbers mean nothing here, and the
                # outage it sat through is of unknown length.
                #
                # Checked before the numeric comparisons and not instead of them, because the two
                # catch different things: this catches a restart the client reconnected *late*
                # from, where the new run has already published past the old cursor and 500 would
                # otherwise look like an ordinary position in the current sequence.
                return Subscription(queue=subscription, resync=True, at=newest)

            if since > newest:
                # Ahead of us. Either a restart from a client too old to send an epoch, or a cursor
                # from a log that has been rolled back — neither is replayable.
                return Subscription(queue=subscription, resync=True, at=newest)

            if since < oldest - 1:
                # Too far behind to replay honestly.
                return Subscription(queue=subscription, resync=True, at=newest)

            missed = [event for event in self._log if event.id > since]
            return Subscription(queue=subscription, missed=missed, at=newest)

    def unsubscribe(self, subscription: Subscription | queue.Queue) -> None:
        target = subscription.queue if isinstance(subscription, Subscription) else subscription
        with self._lock:
            self._subscribers.discard(target)

    # -- for tests and diagnostics ------------------------------------------ #

    @property
    def newest(self) -> int:
        with self._lock:
            return self._log[-1].id if self._log else 0

    @property
    def subscribers(self) -> int:
        with self._lock:
            return len(self._subscribers)


#: One log for the process.
log = Events()


def publish(type: str, data: dict) -> Event | None:
    """Record an event. Never raises — see `Events.publish`."""
    try:
        return log.publish(type, data)
    except Exception:
        return None


def subscribe(since: int | None = None, epoch: str | None = None) -> Subscription:
    return log.subscribe(since, epoch)


def unsubscribe(subscription: Subscription | queue.Queue) -> None:
    log.unsubscribe(subscription)
