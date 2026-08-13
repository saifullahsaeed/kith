"""One stream saying what changed, so the interface can stop asking.

There was exactly one push channel — the activity feed — and everything else asked on its own clock:
eleven `setInterval`s between 1.2 and 20 seconds, each fetching its own endpoint. A task appeared when
the working-task card next polled, a background task when *that* one did, the board when the control
panel got round to it. One reload showed you one of them and a second showed you another.

Underneath that sat a hole no interval closes: a turn the server starts on its own — a reminder
firing, a background task finishing — pushed nothing. The transcript grew on disk, `live_turns` had
the stream ready to be watched, and the open window never learned to attach. Polling cannot fix it,
because none of the things being polled is "a turn just started".

**An event says what changed, never what it changed to.** Carrying the new value would mean a payload
that satisfies every consumer of every shape, versioned forever; and the fetch that follows already
exists and is already right. So: `{"kind": "task"}`, and whoever cares asks.

Modelled on `activity.Feed`, which has been doing exactly this for the feed since before the loop was
removed. The difference is that this carries *kinds* rather than lines, so one subscription serves
every widget instead of one.
"""

from __future__ import annotations

import queue
import threading
from datetime import UTC, datetime

#: What can change. Kept as a tuple rather than free strings so a typo in a publisher is a test
#: failure here rather than a widget that silently never updates.
KINDS = ("turn", "task", "project", "message", "process", "conversation", "workspace")

#: How many events a subscriber may fall behind before the oldest are dropped. A window that has been
#: asleep does not need the whole backlog: every event means "go and ask", and one ask covers all of
#: them. Bounded because an abandoned queue must not grow without limit.
_BACKLOG = 200


class Changes:
    """One publisher for the process, and a set of subscribers reading from queues."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: set[queue.Queue] = set()

    def subscribe(self) -> queue.Queue:
        subscription: queue.Queue = queue.Queue(maxsize=_BACKLOG)
        with self._lock:
            self._subscribers.add(subscription)
        return subscription

    def unsubscribe(self, subscription: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(subscription)

    def publish(self, kind: str, conversation: str = "") -> None:
        """Say that something changed.

        Called from every write path there is, which is why it cannot fail and cannot block. A full
        queue drops its oldest rather than raising: the write is the point and this is the note about
        it, and a subscriber that is behind will refetch anyway — every event means the same thing.
        """
        event = {"kind": kind, "conversation": conversation, "at": datetime.now(UTC).isoformat()}
        with self._lock:
            subscribers = list(self._subscribers)
        for subscription in subscribers:
            try:
                subscription.put_nowait(event)
            except queue.Full:
                # Drop the oldest and take the newest. Losing an event is harmless — the next one
                # prompts the same refetch — but blocking a task being saved is not.
                try:
                    subscription.get_nowait()
                    subscription.put_nowait(event)
                except Exception:
                    pass


stream = Changes()


def subscribe() -> queue.Queue:
    return stream.subscribe()


def unsubscribe(subscription: queue.Queue) -> None:
    stream.unsubscribe(subscription)


def publish(kind: str, conversation: str = "") -> None:
    """Module-level so a write path is one import and one call.

    Never raises. Every caller is in the middle of doing something that matters more than this —
    saving a task, starting a turn — and none of them should carry a try/except for a notification.
    """
    try:
        stream.publish(kind, conversation)
    except Exception:
        pass
