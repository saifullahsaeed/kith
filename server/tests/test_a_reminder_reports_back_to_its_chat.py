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

import pytest

from kith.infra.db import repositories as repo
from kith.services import conversations, session_context
from kith.tools.time import _schedule, _set_reminder


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
