"""A reminder set mid-chat reports back to that chat when it fires.

Before this, a reminder was just `{note, fire_at}` — no memory of which conversation asked
for it. When it came due, the tick picked a conversation by round-robin over whatever was
currently "working", ran a generic autonomy-mode step there, and pushed the result only to
the live Mind feed — an in-memory queue that is gone the moment nobody is subscribed to it.
"Check back in five minutes and tell me" was a promise the system could not keep: not the
wrong conversation seeing the answer, no conversation durably seeing it at all.

Now `set_reminder`/`schedule` capture `session_context.current()` automatically, and a due
reminder that has one runs as a real continuation of *that* chat — same history replay chat
itself uses, result written to the transcript with `conversations.record`, so it is there the
next time that conversation is opened, not only while someone happened to be watching.
"""

from __future__ import annotations

import sys

import pytest

from kith.infra.db import repositories as repo
from kith.services import conversations, session_context
from kith.tools.time import _schedule, _set_reminder


def runner_on(db, monkeypatch):
    """See `test_a_session_that_keeps_working.py` for why this goes through `sys.modules`."""
    module = sys.modules["kith.autonomy.runner"]
    monkeypatch.setattr(module, "AGENT_DB_PATH", db)
    return module.AutonomyRunner()


@pytest.fixture
def no_generic_step(monkeypatch):
    """The generic round-robin step is not what this file is about — silence it so a due
    reminder with no conversation (the backward-compatibility case) does not also try to
    run a real model call."""
    module = sys.modules["kith.autonomy.runner"]
    monkeypatch.setattr(module, "stream_agent", lambda *a, **k: iter(()))
    monkeypatch.setattr(module.repo.tasks, "tasks_awaiting_kith", lambda _p: [])


@pytest.fixture
def stub_chat_reply(monkeypatch):
    """What `_continue_conversation` actually drives — `_turn`'s own `stream_agent`, not the
    tick's. Yields one line of text, which is enough for `_Recorder.finish()` to record a
    real assistant message."""
    from kith.api.routes import chat as chat_module

    def fake_stream_agent(messages, config, host, agent_db_path, **kwargs):
        yield {"type": "delta", "role": "text", "text": "Checked it — still green."}

    monkeypatch.setattr(chat_module, "stream_agent", fake_stream_agent)
    return fake_stream_agent


class TestSetReminderCapturesItsConversation:
    def test_a_reminder_set_mid_chat_remembers_which_chat(self, db):
        conv = conversations.start(db, "hi")["id"]
        with session_context.working_in(conv):
            result = _set_reminder(db, {"note": "check on it", "in_minutes": 5})

        stored = repo.reminders.list_reminders(db)
        assert len(stored) == 1
        assert stored[0]["conversation_id"] == conv
        assert result["conversation_id"] == conv

    def test_a_reminder_set_with_nothing_current_has_none(self, db):
        """A tool called from a test or a script with no `working_in` block open — the
        honest "nobody asked" case, not a conversation named ''."""
        _set_reminder(db, {"note": "check on it", "in_minutes": 5})

        assert repo.reminders.list_reminders(db)[0]["conversation_id"] is None

    def test_a_standing_schedule_captures_it_too(self, db):
        conv = conversations.start(db, "hi")["id"]
        with session_context.working_in(conv):
            _schedule(db, {"note": "daily check", "every_minutes": 60})

        assert repo.schedules.list_schedules(db)[0]["conversation_id"] == conv


class TestADueReminderContinuesItsOwnChat:
    def test_the_reply_lands_in_the_right_conversations_transcript(
        self, db, monkeypatch, no_generic_step, stub_chat_reply
    ):
        conv = conversations.start(db, "keep an eye on the deploy")["id"]
        repo.reminders.add_reminder(db, "2020-01-01T00:00:00+00:00", "check the deploy", conversation_id=conv)
        runner = runner_on(db, monkeypatch)

        runner._tick()

        transcript = conversations.messages(conv)
        # The trigger and the reply are both real messages now, not a line in a feed.
        assert any(m["role"] == "assistant" and "still green" in m["content"] for m in transcript)
        assert any(m["role"] == "user" and "reminders just fired" in m["content"] for m in transcript)

    def test_the_reminder_is_marked_done_not_retried_forever(
        self, db, monkeypatch, no_generic_step, stub_chat_reply
    ):
        conv = conversations.start(db, "hi")["id"]
        added = repo.reminders.add_reminder(db, "2020-01-01T00:00:00+00:00", "check it", conversation_id=conv)
        runner = runner_on(db, monkeypatch)

        runner._tick()

        assert repo.reminders.list_reminders(db, status="pending") == []
        remaining = [r for r in repo.reminders.list_reminders(db) if r["id"] == added["id"]]
        # list_reminders(status=None) with no filter would show it if still pending; confirm
        # via due_reminders instead, which only ever returns "pending".
        assert not repo.reminders.due_reminders(db, "2099-01-01T00:00:00+00:00")

    def test_two_reminders_due_for_the_same_chat_become_one_continuation(
        self, db, monkeypatch, no_generic_step, stub_chat_reply
    ):
        """Not two replies talking past each other — one turn, told about both."""
        conv = conversations.start(db, "hi")["id"]
        repo.reminders.add_reminder(db, "2020-01-01T00:00:00+00:00", "first thing", conversation_id=conv)
        repo.reminders.add_reminder(db, "2020-01-01T00:00:00+00:00", "second thing", conversation_id=conv)
        calls = []
        from kith.api.routes import chat as chat_module

        real_turn = chat_module._turn

        def counting_turn(*a, **k):
            calls.append(1)
            return real_turn(*a, **k)

        monkeypatch.setattr(chat_module, "_turn", counting_turn)
        runner = runner_on(db, monkeypatch)

        runner._tick()

        assert len(calls) == 1
        transcript = conversations.messages(conv)
        trigger = next(m for m in transcript if m["role"] == "user")
        assert "first thing" in trigger["content"]
        assert "second thing" in trigger["content"]

    def test_a_reminder_bound_to_a_conversation_is_not_also_handled_generically(
        self, db, monkeypatch, stub_chat_reply
    ):
        """The old, generic autonomy-mode path must not *also* pick this up — that would be
        two answers to the same reminder, one of them in the wrong place."""
        module = sys.modules["kith.autonomy.runner"]
        generic_calls = []
        monkeypatch.setattr(module, "stream_agent", lambda *a, **k: generic_calls.append(1) or iter(()))
        monkeypatch.setattr(module.repo.tasks, "tasks_awaiting_kith", lambda _p: [])
        conv = conversations.start(db, "hi")["id"]
        repo.reminders.add_reminder(db, "2020-01-01T00:00:00+00:00", "check it", conversation_id=conv)
        runner = runner_on(db, monkeypatch)

        runner._tick()

        assert generic_calls == [], "the generic tick path ran on a reminder that already had a home"

    def test_a_reminder_with_no_conversation_still_uses_the_old_path(self, db, monkeypatch, no_generic_step):
        """Backward compatibility: nothing here should regress a reminder set before this
        feature existed, or one set from outside any chat."""
        module = sys.modules["kith.autonomy.runner"]
        generic_calls = []
        monkeypatch.setattr(module, "stream_agent", lambda *a, **k: generic_calls.append(1) or iter(()))
        repo.reminders.add_reminder(db, "2020-01-01T00:00:00+00:00", "no chat asked for this")
        runner = runner_on(db, monkeypatch)

        runner._tick()

        assert generic_calls == [1], "a conversation-less reminder must still reach the generic path"

    def test_a_failing_continuation_does_not_take_the_tick_down(self, db, monkeypatch, no_generic_step):
        from kith.api.routes import chat as chat_module

        monkeypatch.setattr(chat_module, "_turn", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        conv = conversations.start(db, "hi")["id"]
        repo.reminders.add_reminder(db, "2020-01-01T00:00:00+00:00", "check it", conversation_id=conv)
        runner = runner_on(db, monkeypatch)

        runner._tick()  # must not raise

        # Retired rather than retried forever against a continuation that keeps failing.
        assert not repo.reminders.due_reminders(db, "2099-01-01T00:00:00+00:00")
