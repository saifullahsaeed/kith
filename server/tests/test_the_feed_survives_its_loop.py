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

from kith.services import activity


class TestPublishingWithoutALoop:
    def test_a_line_reaches_a_subscriber(self):
        q = activity.feed.subscribe()
        try:
            activity.feed.publish("tool", "read a file", conversation="c1")
            line = q.get(timeout=1)
        finally:
            activity.feed.unsubscribe(q)
        assert line["kind"] == "tool"
        assert line["text"] == "read a file"
        assert line["conversation"] == "c1"

    def test_recent_keeps_what_was_published(self):
        activity.feed.publish("reply", "you: hello", conversation="c2")
        assert any(item["text"] == "you: hello" for item in activity.feed.recent())

    def test_an_unsubscribed_reader_stops_receiving(self):
        q = activity.feed.subscribe()
        activity.feed.unsubscribe(q)
        activity.feed.publish("tool", "after", conversation="c3")
        assert q.qsize() == 0

    def test_a_line_with_no_conversation_omits_the_key(self):
        """Global lines — a status change — have no session, and the key is left off rather
        than set empty. The panel reads a missing `conversation` as "belongs to whatever you
        are looking at", so this is the shape it depends on, not an oversight."""
        q = activity.feed.subscribe()
        try:
            activity.feed.publish("status", "ready")
            line = q.get(timeout=1)
        finally:
            activity.feed.unsubscribe(q)
        assert "conversation" not in line
        assert line["kind"] == "status"


class TestTheToolRenderersCameToo:
    def test_a_call_reads_as_prose(self):
        assert "notes.md" in activity.describe_call("read_file", {"path": "notes.md"})

    def test_arguments_are_shortened_for_the_line(self):
        short = activity.short_args({"path": "notes.md", "body": "x" * 5_000})
        assert len(str(short)) < 1_000
