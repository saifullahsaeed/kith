"""A conversation leaves the same trail an unattended step does.

Chat used to leave none. Every mode the runner has writes two things — a line on the live
Mind feed and a row in the durable flight recorder — and the one path where most of the real
work happens wrote neither. So "what has he been doing" could be answered for the steps
nobody watched and not for the afternoon you spent together, and a tick costing 40k tokens
was visible in /api/activity while a chat turn costing 200k was not.

It matters more now the Mind panel is per session: a conversation you never left working
would otherwise show an empty panel forever, which reads as broken rather than as "he has
not gone off on his own here".
"""

from __future__ import annotations

import pytest

from kith.api.routes import chat as chat_route
from kith.infra.db import repositories as repo


@pytest.fixture
def feed(db, monkeypatch):
    """A _MindFeed writing to a temp database, with what it publishes captured.

    Patched through `sys.modules` rather than by setting an attribute: the package
    re-exports the singleton, so `kith.services.activity` as an *attribute* is the instance
    while `from kith.services.activity import feed` reads the module out of sys.modules.
    Patching the attribute looks like it works and changes nothing — three tests once ran
    against the live database that way.
    """
    import sys

    published: list[dict] = []
    monkeypatch.setattr(chat_route, "AGENT_DB_PATH", db)

    class FakeFeed:
        def publish(self, kind, text, **fields):
            published.append({"kind": kind, "text": text, **fields})

        def charge_session(self, conversation_id, uncached_in, cost_usd):
            return False  # never over budget; the cap has its own tests

    monkeypatch.setitem(sys.modules, "kith.services.activity", type("M", (), {"feed": FakeFeed()})())
    return published


def a_turn(conversation_id="c1", opening="write me a haiku"):
    return chat_route._MindFeed(conversation_id, opening)


class TestTheLiveFeed:
    def test_a_turn_opens_with_what_you_said(self, feed):
        a_turn()
        assert feed[0]["kind"] == "reply"
        assert "write me a haiku" in feed[0]["text"]

    def test_every_line_says_which_session_it_belongs_to(self, feed):
        watcher = a_turn("session-7")
        watcher.saw({"type": "tool_call", "name": "shell", "arguments": {"command": "ls"}})
        watcher.finish()
        # The whole point: without this the panel cannot tell one conversation's work from
        # another's, which is what it looked like with two projects going.
        assert {line["conversation"] for line in feed} == {"session-7"}

    def test_a_tool_call_arrives_as_data_not_as_a_sentence(self, feed):
        watcher = a_turn()
        watcher.saw({"type": "tool_call", "name": "read_file", "arguments": {"path": "notes.md"}})
        call = next(line for line in feed if line["kind"] == "tool")
        # Parsed back out of prose is how the interface used to get this, and it threw away
        # which file — the only interesting part.
        assert call["tool"] == "read_file"
        assert call["args"]["path"] == "notes.md"

    def test_token_counts_come_per_request(self, feed):
        watcher = a_turn()
        for _ in range(2):
            watcher.saw(
                {
                    "type": "stats",
                    "stats": {
                        "promptTokens": 1000,
                        "uncachedTokens": 200,
                        "cachedTokens": 800,
                        "responseTokens": 50,
                    },
                }
            )
        rounds = [line for line in feed if line["kind"] == "tokens"]
        assert [line["tokens"]["round"] for line in rounds] == [1, 2]
        assert rounds[0]["tokens"] == {"round": 1, "uncached": 200, "cached": 800, "out": 50}

    def test_it_closes_the_turn(self, feed):
        watcher = a_turn()
        watcher.finish()
        assert feed[-1]["kind"] == "done"

    def test_a_feed_that_throws_does_not_take_the_turn_down(self, db, monkeypatch):
        """A line on a panel is never worth losing an answer over."""
        import sys

        monkeypatch.setattr(chat_route, "AGENT_DB_PATH", db)

        class Broken:
            def publish(self, *_a, **_k):
                raise RuntimeError("no feed today")

        monkeypatch.setitem(sys.modules, "kith.services.activity", type("M", (), {"feed": Broken()})())
        watcher = a_turn()
        watcher.saw({"type": "tool_call", "name": "shell", "arguments": {}})
        watcher.finish()  # no exception is the assertion


class TestTheFlightRecorder:
    def test_a_turn_leaves_a_row(self, db, feed):
        watcher = a_turn(opening="check the build")
        watcher.saw({"type": "tool_call", "name": "check_code", "arguments": {}})
        watcher.saw({"type": "delta", "role": "text", "text": "Clean."})
        watcher.finish()

        rows = repo.messages.list_turn_log(db, 10)
        assert len(rows) == 1
        assert rows[0]["mode"] == "chat"
        assert rows[0]["tools"] == ["check_code"]
        assert rows[0]["outcome"] == "Clean."
        assert rows[0]["focus"] == "check the build"

    def test_it_records_what_the_turn_cost(self, db, feed):
        watcher = a_turn()
        watcher.saw(
            {
                "type": "stats",
                "stats": {
                    "promptTokens": 38_000,
                    "uncachedTokens": 7_000,
                    "cachedTokens": 31_000,
                    "responseTokens": 900,
                },
            }
        )
        watcher.finish()

        row = repo.messages.list_turn_log(db, 1)[0]
        # Both numbers, because they answer different questions: what he was shown, and what
        # a provider actually had to read.
        assert (row["tokens_in"], row["tokens_out"], row["tokens_uncached"]) == (38_000, 900, 7_000)

    def test_a_failed_turn_says_so(self, db, feed):
        watcher = a_turn()
        watcher.saw({"type": "error", "message": "provider timed out"})
        watcher.finish()
        assert repo.messages.list_turn_log(db, 1)[0]["outcome"] == "error: provider timed out"

    def test_a_turn_that_only_talked_still_gets_a_row(self, db, feed):
        """Answering a question is work. A row only for turns that used tools would make the
        recorder agree with the old "capture, don't do" rule that was just removed."""
        watcher = a_turn(opening="how are you")
        watcher.saw({"type": "delta", "role": "text", "text": "Fine — quiet morning."})
        watcher.finish()
        assert repo.messages.list_turn_log(db, 1)[0]["mode"] == "chat"

    def test_chat_shows_up_in_the_summary_alongside_the_ticks(self, db, feed):
        a_turn().finish()
        summary = repo.messages.turn_log_summary(db)
        assert summary["ticks"] == 1
