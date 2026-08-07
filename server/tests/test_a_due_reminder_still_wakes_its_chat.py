"""A reminder wakes its own conversation, with nothing looping.

`_fire_conversation_reminders` lived inside `_tick` for one reason: `_tick` was the only
thing running. It never chose work and never read a task — it asked "is anything due?" and
continued the chat that thing belonged to. Pulling it out is what lets the loop go without
taking "remind me at 3pm" with it.
"""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo
from kith.services import conversations, scheduler


class TestWhatIsDueWakesItsOwnChat:
    def test_a_due_reminder_continues_that_conversation(self, db: Path, monkeypatch):
        monkeypatch.setattr(scheduler, "AGENT_DB_PATH", db)
        woken: list[str] = []
        monkeypatch.setattr(scheduler, "_continue", lambda cid, notes: woken.append(cid))
        conv = conversations.start(db, "hi")["id"]
        repo.reminders.add_reminder(db, "2020-01-01T00:00:00+00:00", "check it", conversation_id=conv)

        scheduler.fire_due("2030-01-01T00:00:00+00:00")

        assert woken == [conv]

    def test_a_reminder_with_no_conversation_is_left_alone(self, db: Path, monkeypatch):
        """It belongs to nobody's chat, so there is no chat to continue. Before, the loop
        would eventually pick it up on a generic step; there is no generic step now."""
        monkeypatch.setattr(scheduler, "AGENT_DB_PATH", db)
        woken: list[str] = []
        monkeypatch.setattr(scheduler, "_continue", lambda cid, notes: woken.append(cid))
        repo.reminders.add_reminder(db, "2020-01-01T00:00:00+00:00", "no chat")

        scheduler.fire_due("2030-01-01T00:00:00+00:00")

        assert woken == []

    def test_two_reminders_for_one_chat_wake_it_once(self, db: Path, monkeypatch):
        """Not two replies talking past each other — one turn, told about both."""
        monkeypatch.setattr(scheduler, "AGENT_DB_PATH", db)
        seen: list[tuple[str, int]] = []
        monkeypatch.setattr(scheduler, "_continue", lambda cid, notes: seen.append((cid, len(notes))))
        conv = conversations.start(db, "hi")["id"]
        repo.reminders.add_reminder(db, "2020-01-01T00:00:00+00:00", "first", conversation_id=conv)
        repo.reminders.add_reminder(db, "2020-01-01T00:00:00+00:00", "second", conversation_id=conv)

        scheduler.fire_due("2030-01-01T00:00:00+00:00")

        assert seen == [(conv, 2)]

    def test_nothing_due_wakes_nobody(self, db: Path, monkeypatch):
        monkeypatch.setattr(scheduler, "AGENT_DB_PATH", db)
        woken: list[str] = []
        monkeypatch.setattr(scheduler, "_continue", lambda cid, notes: woken.append(cid))
        conversations.start(db, "hi")

        scheduler.fire_due("2030-01-01T00:00:00+00:00")

        assert woken == []

    def test_a_fired_reminder_is_retired(self, db: Path, monkeypatch):
        """Fires once, whether or not the continuation worked. One that keeps failing is a
        bug to find in the traceback, not a reason to retry it forever."""
        monkeypatch.setattr(scheduler, "AGENT_DB_PATH", db)
        monkeypatch.setattr(scheduler, "_continue", lambda cid, notes: None)
        conv = conversations.start(db, "hi")["id"]
        repo.reminders.add_reminder(db, "2020-01-01T00:00:00+00:00", "check it", conversation_id=conv)

        scheduler.fire_due("2030-01-01T00:00:00+00:00")

        assert repo.reminders.due_reminders(db, "2030-01-01T00:00:00+00:00") == []

    def test_a_continuation_that_throws_does_not_stop_the_others(self, db: Path, monkeypatch):
        """Every other conversation waiting on a reminder still gets its turn."""
        monkeypatch.setattr(scheduler, "AGENT_DB_PATH", db)
        reached: list[str] = []

        def explode(cid, notes):
            reached.append(cid)
            raise RuntimeError("provider is down")

        monkeypatch.setattr(scheduler, "_continue", explode)
        first = conversations.start(db, "one")["id"]
        second = conversations.start(db, "two")["id"]
        repo.reminders.add_reminder(db, "2020-01-01T00:00:00+00:00", "a", conversation_id=first)
        repo.reminders.add_reminder(db, "2020-01-01T00:00:00+00:00", "b", conversation_id=second)

        scheduler.fire_due("2030-01-01T00:00:00+00:00")

        assert sorted(reached) == sorted([first, second])


class TestTheTimerDoesNotStartOnImport:
    def test_importing_the_module_starts_nothing(self):
        """Importing must never start a thread — see `test_the_suite_does_not_start_loops`.
        The hazard the loop introduced outlives the loop, because this has a timer too."""
        assert scheduler._thread is None
