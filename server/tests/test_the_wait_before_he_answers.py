"""Nothing slow happens between hitting send and the stream opening.

The gap this is about was real and invisible. `_build_messages` calls `history.fold()`, and on
a long conversation a fold is a *summarisation request to the model* — measured on a real
transcript, 1.57M characters of backlog, about 390k tokens, a full round-trip. It ran in the
request thread, before the turn thread started, so:

* nothing was on the wire yet, so the interface showed a dead composer;
* the `compacting` event that drives the "he is folding" indicator is emitted by `_run_turn`,
  which had not been reached — so the one thing that could have explained the wait was
  structurally unable to fire.

It reads as "he takes ages before he responds", and every explanation you reach for first —
reasoning effort, a slow provider — is wrong, because those come *after* this.

So: the request thread does only cheap work, and everything that can block moves onto the turn
thread where it can say what it is doing.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from kith.api.routes import chat as route
from kith.services import conversations


def _chat(payload: dict):
    """Call the view the way Flask would.

    `chat()` carries `@api.input(..., arg_name="payload")`, so the decorator reads the body and
    passes it in itself — handing it one as well is what "multiple values for argument
    'payload'" means. It needs a request context to read that body out of.
    """
    from flask import Flask

    with Flask(__name__).test_request_context("/api/chat", json=payload):
        return route.chat()


@pytest.fixture(autouse=True)
def _no_turn_thread_outlives_the_test():
    """Wait for the turn threads these tests start, before handing the process on.

    `chat()` finishes by starting a daemon thread named `kith-turn-<conversation id>` and
    returning — which is the whole point of this file, and it means every test in it leaves one
    running. Usually it is done microseconds later and nobody notices.

    `test_the_suite_does_not_start_loops` notices. It asserts no `kith-` thread survives its
    file, for the reason its own docstring gives: a thread that outlives `monkeypatch` reads
    the *real* `AGENT_DB_PATH`, and one that reaches a due reminder makes a real network call
    against the real board. Alphabetically that guard sorts before this file — `…suite…` before
    `…wait…` — so in the fixed order pytest runs by default it has never once seen these. Run
    the suite in any other order and it fails about one time in three; the threads are only
    still alive when the machine is busy enough, which is exactly when a full suite runs.

    Joining rather than asserting: this file's threads are legitimate, and it is their owner's
    job to see them out. The guard stays the guard.
    """
    yield
    for thread in threading.enumerate():
        if thread.name.startswith("kith-turn-"):
            thread.join(timeout=5)


@pytest.fixture
def feed(db: Path, monkeypatch):
    published: list[dict] = []
    monkeypatch.setattr(route, "AGENT_DB_PATH", db)

    class FakeFeed:
        def publish(self, kind, text, **fields):
            published.append({"kind": kind, "text": text, **fields})

        def charge_session(self, conversation_id, uncached_in, cost_usd):
            return False

    monkeypatch.setattr(route, "feed", FakeFeed())
    return published


class TestTheRequestThreadDoesNothingSlow:
    def test_the_view_returns_without_waiting_for_the_fold(self, db: Path, feed, monkeypatch):
        """The whole point, and it has to be measured rather than inferred.

        An earlier version of this asserted that the first line out of the stream was the
        `conversation` event while a slow fold was blocked. That passed against the *broken*
        code: the fold blocked the request thread for its full five seconds, and only then did
        anything happen — first line included. Every assertion held and the bug was untouched.

        What actually distinguishes the two is *when the view returns*. On the request path it
        cannot return until the fold finishes; on the turn thread it returns immediately.
        """
        release = threading.Event()

        def slow_fold(*args, **kwargs):
            release.wait(timeout=5)
            return list(args[0]), None

        monkeypatch.setattr(route.history, "fold", slow_fold)
        monkeypatch.setattr(route, "stream_agent", lambda *a, **k: iter(()))

        conv = conversations.start(db, "hi")["id"]
        began = time.perf_counter()
        response = _chat({"messages": [{"role": "user", "content": "go"}], "conversationId": conv})
        took = time.perf_counter() - began

        try:
            assert took < 1.0, (
                f"the view took {took:.1f}s to return — it is still waiting on the fold, which is "
                "the whole bug"
            )
            first = next(iter(response.response))
            assert json.loads(first)["type"] == "conversation"
        finally:
            release.set()

    def test_the_fold_says_it_is_folding(self, db: Path, feed, monkeypatch):
        """Silence is the failure. A wait you can see is a wait you can forgive."""
        monkeypatch.setattr(
            route.history, "fold", lambda *a, **k: (list(a[0]), {"through": 2, "text": "a brief"})
        )
        monkeypatch.setattr(route, "stream_agent", lambda *a, **k: iter(()))

        conv = conversations.start(db, "hi")["id"]
        response = _chat({"messages": [{"role": "user", "content": "go"}], "conversationId": conv})
        kinds = [json.loads(line)["type"] for line in response.response]

        assert "compacting" in kinds, f"a fold happened and nothing said so: {kinds}"

    def test_a_turn_that_does_not_fold_says_nothing(self, db: Path, feed, monkeypatch):
        """The indicator has to mean something, so it must not fire on every turn."""
        monkeypatch.setattr(route.history, "fold", lambda *a, **k: (list(a[0]), None))
        monkeypatch.setattr(route, "stream_agent", lambda *a, **k: iter(()))

        conv = conversations.start(db, "hi")["id"]
        response = _chat({"messages": [{"role": "user", "content": "go"}], "conversationId": conv})
        kinds = [json.loads(line)["type"] for line in response.response]

        assert "compacting" not in kinds

    def test_the_user_message_is_recorded_before_the_stream_opens(self, db: Path, feed, monkeypatch):
        """Moving work off the request path must not move *this*: a turn that dies still has to
        leave what you said in the transcript."""
        monkeypatch.setattr(route.history, "fold", lambda *a, **k: (list(a[0]), None))
        monkeypatch.setattr(route, "stream_agent", lambda *a, **k: iter(()))

        conv = conversations.start(db, "hi")["id"]
        _chat({"messages": [{"role": "user", "content": "remember this"}], "conversationId": conv})

        said = [m["content"] for m in conversations.full_messages(conv) if m["role"] == "user"]
        assert "remember this" in said


class TestTheMeterTellsTheTruth:
    def test_a_folded_turn_reports_what_it_folded_away(self, db: Path, feed, monkeypatch):
        """The meter measures the conversation *after* folding, so a conversation 4.6x over its
        window reads as comfortable and the fold looks gratuitous. The reading has to carry the
        size that made the fold necessary, or the one number a person checks is the one number
        that cannot show the problem."""
        monkeypatch.setattr(
            route.history, "fold", lambda *a, **k: (list(a[0])[:1], {"through": 9, "text": "brief"})
        )
        monkeypatch.setattr(route, "stream_agent", lambda *a, **k: iter(()))

        conv = conversations.start(db, "hi")["id"]
        # A turn already in the transcript, so the stub above — which keeps the first message and
        # drops the rest — has something to drop.
        #
        # This used to pass on an empty conversation, and it is worth saying why: the prompt
        # carried the message just typed *twice*, because it was recorded and then appended
        # again. The fold was removing the duplicate. Fixed in `_history_for_turn`, and the test
        # went red immediately — it had been measuring a bug. See `test_he_is_asked_once.py`.
        conversations.record(db, conv, "user", "the first thing")
        conversations.record(db, conv, "assistant", "the first answer")
        response = _chat({"messages": [{"role": "user", "content": "go"}], "conversationId": conv})
        events = [json.loads(line) for line in response.response]

        folded = [e for e in events if e["type"] == "compacting"]
        assert folded, "no fold event"
        assert folded[0]["foldedFrom"] > folded[0]["foldedTo"], (
            "the event has to say how much was dropped, or the meter keeps lying"
        )
