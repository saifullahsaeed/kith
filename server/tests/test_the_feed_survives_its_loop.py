"""The Mind feed outlives the loop it was written inside.

`_MindFeed` in the chat route publishes every chat turn through what used to be
`runner.publish`. The runner also happened to contain the loop that picked work on its own.
Deleting that loop must not take the feed down with it — a conversation is work too, and it
was once the one kind that left no trace here.

The tool-call renderers come with it for the same reason. `_describe_call` turns
`{"path": "notes.md"}` into "read `notes.md`", and it lived under `autonomy/` only because
that is where the first caller was. Without it the panel prints JSON at you.
"""

from __future__ import annotations

from kith.kernel import events
from kith.services import activity


class TestPublishingWithoutALoop:
    """The feed no longer has subscribers of its own.

    It had a set of queues and a fan-out loop, which was `kernel/changes`'s mechanism written a
    second time with a different payload. Both are `kernel/events` now, so a line goes out as an
    `activity` event with an id on it — and these tests read it from there. What the feed still
    owns is `recent()`, the snapshot a window opens with.
    """

    def test_a_line_reaches_a_subscriber(self):
        subscription = events.subscribe()
        try:
            activity.feed.publish("tool", "read a file", conversation="c1")
            event = subscription.queue.get(timeout=1)
        finally:
            events.unsubscribe(subscription)
        assert event.type == "activity"
        assert event.data["kind"] == "tool"
        assert event.data["text"] == "read a file"
        assert event.data["conversation"] == "c1"

    def test_recent_keeps_what_was_published(self):
        activity.feed.publish("reply", "you: hello", conversation="c2")
        assert any(item["text"] == "you: hello" for item in activity.feed.recent())

    def test_an_unsubscribed_reader_stops_receiving(self):
        subscription = events.subscribe()
        events.unsubscribe(subscription)
        activity.feed.publish("tool", "after", conversation="c3")
        assert subscription.queue.qsize() == 0

    def test_recent_is_not_replayed_to_a_new_subscriber(self):
        """The bug that made the feed repeat itself.

        The old stream sent `feed.recent()` to every connection, so each reconnect appended the
        last hundred lines again — with no id on the wire, the client could not tell they were the
        same lines. A new subscriber now gets what happens next and fetches the backlog through
        `GET /api/activity`, which is the ordinary snapshot-then-subscribe shape.
        """
        activity.feed.publish("tool", "before you joined")
        subscription = events.subscribe()
        try:
            assert subscription.missed == []
            assert subscription.queue.qsize() == 0
        finally:
            events.unsubscribe(subscription)

    def test_a_line_with_no_conversation_omits_the_key(self):
        """Global lines — a status change — have no session, and the key is left off rather
        than set empty. The panel reads a missing `conversation` as "belongs to whatever you
        are looking at", so this is the shape it depends on, not an oversight."""
        subscription = events.subscribe()
        try:
            activity.feed.publish("status", "ready")
            event = subscription.queue.get(timeout=1)
        finally:
            events.unsubscribe(subscription)
        assert "conversation" not in event.data
        assert event.data["kind"] == "status"


class TestTheToolRenderersCameToo:
    def test_a_call_reads_as_prose(self):
        assert "notes.md" in activity.describe_call("read_file", {"path": "notes.md"})

    def test_arguments_are_shortened_for_the_line(self):
        short = activity.short_args({"path": "notes.md", "body": "x" * 5_000})
        assert len(str(short)) < 1_000
