"""A dropped connection must cost a beat, not a screen.

This is the mechanism that lets the interface stop polling, so it is the mechanism that has to be
right. Eleven `setInterval`s were never laziness — they were the safety net under a channel that
could silently drop, and the two channels that existed dropped in opposite directions:

* `/api/changes` threw its queue away on disconnect, so a laptop waking up lost everything that
  happened while it slept and the window sat there looking current.
* `/api/activity/stream` re-sent the whole recent buffer to every new connection, with no id on the
  wire and no dedupe on the client, so a reconnect duplicated the last hundred lines into the feed.

Both are the same missing thing: the events had no identity. With ids, the three ways a cursor can
be wrong all have honest answers, and every one of them is asserted here — because "the client
resyncs when it has to" is the promise the deleted timers were standing in for.
"""

from __future__ import annotations

import pytest

from kith.kernel import changes, events


@pytest.fixture
def log():
    """A fresh log per test. The module-level one is shared, and ids are what is under test."""
    return events.Events(backlog=5)


@pytest.fixture(autouse=True)
def no_leftover_subscribers():
    yield
    events.log._subscribers.clear()


class TestIdentity:
    def test_every_event_gets_the_next_id(self, log):
        first = log.publish("changed", {"kind": "task"})
        second = log.publish("changed", {"kind": "task"})
        assert (first.id, second.id) == (1, 2)

    def test_ids_do_not_restart_when_the_log_rolls_over(self, log):
        """The log is bounded; the numbering is not. An id that came round again would make a
        stale cursor look current, which is the one thing it must never look."""
        published = [log.publish("changed", {"kind": "task"}) for _ in range(12)]
        assert published[-1].id == 12
        assert len(log._log) == 5

    def test_the_type_is_on_the_event(self, log):
        """One connection carries both kinds, so the type has to travel with the payload rather
        than be implied by which endpoint it arrived on."""
        assert log.publish("activity", {"kind": "tool"}).type == "activity"


class TestJoining:
    def test_a_fresh_subscriber_gets_nothing_it_missed(self, log):
        """It fetches its own state through the API. Replaying a backlog it never saw is exactly
        how the activity feed came to repeat itself."""
        log.publish("activity", {"text": "before you joined"})
        subscription = log.subscribe()
        assert subscription.missed == []
        assert subscription.resync is False

    def test_a_subscriber_hears_what_comes_after(self, log):
        subscription = log.subscribe()
        log.publish("changed", {"kind": "task"})
        assert subscription.queue.get_nowait().data == {"kind": "task"}

    def test_joining_reports_where_the_log_is(self, log):
        """So a `resync` can hand back a cursor that is current instead of leaving the client to
        guess one and come back wrong."""
        log.publish("changed", {"kind": "task"})
        assert log.subscribe().at == 1


class TestResuming:
    def test_a_cursor_behind_the_log_replays_the_tail(self, log):
        for kind in ("task", "project", "message"):
            log.publish("changed", {"kind": kind})
        subscription = log.subscribe(since=1)
        assert [event.data["kind"] for event in subscription.missed] == ["project", "message"]
        assert subscription.resync is False

    def test_a_cursor_at_the_head_replays_nothing(self, log):
        log.publish("changed", {"kind": "task"})
        subscription = log.subscribe(since=1)
        assert subscription.missed == []
        assert subscription.resync is False

    def test_replay_stops_and_the_live_queue_takes_over(self, log):
        """The seam. Reading the backlog and joining the fan-out happen under one lock, so an
        event published between them is neither lost nor delivered twice — which were the two
        bugs this module exists to end, and are both invisible in a passing test that only
        checks the tail."""
        log.publish("changed", {"kind": "task"})
        subscription = log.subscribe(since=0)
        log.publish("changed", {"kind": "project"})

        replayed = [event.data["kind"] for event in subscription.missed]
        live = [subscription.queue.get_nowait().data["kind"]]
        assert replayed == ["task"]
        assert live == ["project"]

    def test_a_cursor_older_than_the_log_asks_for_a_resync(self, log):
        """Nine events into a log that holds five: there is no honest replay, so it says so
        rather than letting the client believe it is up to date."""
        for _ in range(9):
            log.publish("changed", {"kind": "task"})
        subscription = log.subscribe(since=1)
        assert subscription.resync is True
        assert subscription.missed == []

    def test_a_cursor_ahead_of_the_log_asks_for_a_resync(self):
        """The restart case, caught by the numbers alone — which only works while the new run is
        still behind where the client got to."""
        fresh = events.Events()
        fresh.publish("changed", {"kind": "task"})
        assert fresh.subscribe(since=400).resync is True

    def test_a_cursor_from_another_run_asks_for_a_resync(self, log):
        """The same restart, reconnected to late — and the case the numbers cannot catch.

        Once the fresh process has published past the old cursor, that cursor looks like an
        ordinary position: replaying from it would hand the client events from a sequence it has
        never seen and call it caught up. The epoch is what makes the two distinguishable, and it
        is checked before the numbers rather than instead of them.
        """
        for _ in range(3):
            log.publish("changed", {"kind": "task"})
        inside = log.newest

        assert log.subscribe(since=inside - 1, epoch="not-this-run").resync is True
        # And the same position, claimed by this run, is replayed rather than refused.
        current = log.subscribe(since=inside - 1, epoch=events.EPOCH)
        assert current.resync is False
        assert len(current.missed) == 1

    def test_a_cursor_with_no_epoch_still_gets_the_numeric_checks(self, log):
        """A client too old to send one keeps exactly the behaviour it had."""
        log.publish("changed", {"kind": "task"})
        assert log.subscribe(since=0).resync is False
        assert log.subscribe(since=999).resync is True

    def test_a_resync_carries_the_current_cursor(self):
        fresh = events.Events()
        for _ in range(3):
            fresh.publish("changed", {"kind": "task"})
        assert fresh.subscribe(since=900).at == 3


class TestLoss:
    def test_an_overflowing_subscriber_leaves_a_gap_rather_than_blocking(self, log):
        """A queue nobody is draining must not stop a task being saved. It drops its oldest, and
        the client notices the ids are out of step and resyncs itself — so every loss lands in a
        case the client already handles."""
        subscription = log.subscribe()
        for _ in range(20):
            log.publish("changed", {"kind": "task"})

        heard = []
        while not subscription.queue.empty():
            heard.append(subscription.queue.get_nowait().id)

        assert len(heard) == 5, heard
        assert heard == [16, 17, 18, 19, 20]
        assert heard[0] != 1, "a gap, and the ids are what make it visible"

    def test_publishing_never_raises(self, log):
        """Every write path calls this in the middle of something that matters more."""
        log.subscribe()
        for _ in range(1_000):
            log.publish("changed", {"kind": "task"})


class TestTheVocabularyGoesThroughTheLog:
    def test_a_change_arrives_as_a_changed_event(self):
        subscription = events.subscribe()
        try:
            changes.publish("task", conversation="c-1")
            event = subscription.queue.get(timeout=1)
        finally:
            events.unsubscribe(subscription)
        assert event.type == "changed"
        assert event.data == {"kind": "task", "conversation": "c-1"}

    def test_the_conversation_key_is_always_there(self):
        """Empty when the change is about the machine rather than one chat. A key that comes and
        goes is a key every reader has to guard."""
        subscription = events.subscribe()
        try:
            changes.publish("task")
            event = subscription.queue.get(timeout=1)
        finally:
            events.unsubscribe(subscription)
        assert event.data["conversation"] == ""

    def test_a_change_carries_no_value(self):
        """The rule that keeps one shape here however many consumers appear: an event says what
        moved, and whoever cares asks through the endpoint it already uses."""
        subscription = events.subscribe()
        try:
            changes.publish("task")
            event = subscription.queue.get(timeout=1)
        finally:
            events.unsubscribe(subscription)
        assert set(event.data) == {"kind", "conversation"}
