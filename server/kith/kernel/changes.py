"""What changed, in the vocabulary the write paths use.

This module used to be a second fan-out mechanism of its own — its own subscriber set, its own
queues, its own drop policy — sitting beside the activity feed's, which was the same code with a
different payload. `kernel/events` is that mechanism now, once, with the identity both were
missing. What is left here is the part that was actually about the domain: the list of things that
can change, and a one-line way for a repository to say one of them did.

**An event says what changed, never what it changed to.** Carrying the new value would mean a
payload that satisfies every consumer of every shape, versioned forever — and the fetch that
follows already exists and is already right. So: `{"kind": "task"}`, and whoever cares asks.

**And it says the kind, not the row.** Which is a deliberate stop, because the obvious next step is
wrong: `notifies` could read an id off the value a write returns, but `set_checklist_item` returns
the *checklist item*, so a "task" event would carry an id belonging to something else and the client
would refetch the wrong task with total confidence. A kind is coarse and always true. An id that is
sometimes the wrong entity is worse than no id, and the interface refetches a list either way.
"""

from __future__ import annotations

from kith.kernel import events

#: What can change. Kept as a tuple rather than free strings so a typo in a publisher is a test
#: failure rather than a widget that silently never updates — see
#: `test_every_kind_has_a_publisher_and_every_publisher_a_kind`, which asserts both directions,
#: because both fail quietly: a misspelled kind updates nothing, and a declared kind nobody
#: publishes is a subscription that can never fire.
#:
#: `question` and `permission` were the two that were missing, and their absence had a cost you
#: could see: with nothing to listen for, the card that asks you something polled every 1.2
#: seconds and the one asking to be allowed something polled every 2.5, forever, on the chance
#: that one had appeared. A kind each, published where the state actually moves, and both timers
#: could go — which is the difference between a push channel and a push channel the interface
#: still does not trust.
KINDS = (
    "turn",
    "task",
    "project",
    "message",
    "process",
    "workspace",
    "question",
    "permission",
)


def publish(kind: str, conversation: str = "") -> None:
    """Say that something changed.

    Module-level so a write path is one import and one call, and swallowing everything because
    every caller is in the middle of something that matters more than this. A save must never fail
    because nobody was listening to the note about it.
    """
    # `conversation` is always present, empty when the change is about the machine rather than
    # about one chat. A key that comes and goes is a key every reader has to guard.
    events.publish("changed", {"kind": kind, "conversation": conversation})
