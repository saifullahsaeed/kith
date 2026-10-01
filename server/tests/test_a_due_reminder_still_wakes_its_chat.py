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


def _resume(conversation_id: str, trigger: str) -> None:
    """The turn a due reminder would run. Injected now rather than imported: `fire_due` is
    handed how to wake a conversation, so a test that only cares *which* one was woken
    supplies a no-op instead of stubbing a route's private functions."""


class TestWhatIsDueWakesItsOwnChat:
    def test_a_due_reminder_continues_that_conversation(self, db: Path, monkeypatch):
        monkeypatch.setattr(scheduler, "AGENT_DB_PATH", db)
        woken: list[str] = []
        monkeypatch.setattr(scheduler, "_continue", lambda cid, notes, resume, **_: woken.append(cid))
        conv = conversations.start(db, "hi")["id"]
        repo.reminders.add_reminder(db, "2020-01-01T00:00:00+00:00", "check it", conversation_id=conv)

        scheduler.fire_due("2030-01-01T00:00:00+00:00", _resume)

        assert woken == [conv]

    def test_a_reminder_with_no_conversation_starts_one(self, db: Path, monkeypatch):
        """Set from the Control Panel, it belongs to no chat. It used to be left alone for that
        reason (2026-08-08: "there is no chat to continue") — which, once the panel could set
        reminders, meant one that never fired and never retired. Now firing starts its chat."""
        monkeypatch.setattr(scheduler, "AGENT_DB_PATH", db)
        woken: list[str] = []
        monkeypatch.setattr(scheduler, "_continue", lambda cid, notes, resume, **_: woken.append(cid))
        made = repo.reminders.add_reminder(db, "2020-01-01T00:00:00+00:00", "check the build")

        scheduler.fire_due("2030-01-01T00:00:00+00:00", _resume)

        assert len(woken) == 1
        assert conversations.get(db, woken[0])["title"].startswith("check the build")
        [row] = [r for r in repo.reminders.list_reminders(db) if r["id"] == made["id"]]
        assert row["conversation_id"] == woken[0]
        assert row["status"] == "done"

    def test_a_standing_job_with_no_conversation_keeps_the_one_it_started(self, db: Path, monkeypatch):
        """Its later runs post to the same thread, not a new chat a day."""
        monkeypatch.setattr(scheduler, "AGENT_DB_PATH", db)
        woken: list[str] = []
        monkeypatch.setattr(scheduler, "_continue", lambda cid, notes, resume, **_: woken.append(cid))
        job = repo.schedules.add_schedule(db, "check CI", "2020-01-01T00:00:00+00:00", every_minutes=60)

        scheduler.fire_due("2030-01-01T00:00:00+00:00", _resume)

        [row] = [s for s in repo.schedules.list_schedules(db) if s["id"] == job["id"]]
        assert row["conversation_id"] == woken[0]

    def test_a_reminder_set_from_the_panel_can_name_its_chat(self, db: Path, monkeypatch):
        """A "remind again" carries the original's chat, so it reports back where it began."""
        from kith.services import brain

        conv = conversations.start(db, "hi")["id"]
        made = brain.create(db, "reminder", {"note": "again", "in_minutes": 5, "conversation_id": conv})
        assert made["conversation_id"] == conv

    def test_two_reminders_for_one_chat_wake_it_once(self, db: Path, monkeypatch):
        """Not two replies talking past each other — one turn, told about both."""
        monkeypatch.setattr(scheduler, "AGENT_DB_PATH", db)
        seen: list[tuple[str, int]] = []
        monkeypatch.setattr(
            scheduler, "_continue", lambda cid, notes, resume, **_: seen.append((cid, len(notes)))
        )
        conv = conversations.start(db, "hi")["id"]
        repo.reminders.add_reminder(db, "2020-01-01T00:00:00+00:00", "first", conversation_id=conv)
        repo.reminders.add_reminder(db, "2020-01-01T00:00:00+00:00", "second", conversation_id=conv)

        scheduler.fire_due("2030-01-01T00:00:00+00:00", _resume)

        assert seen == [(conv, 2)]

    def test_nothing_due_wakes_nobody(self, db: Path, monkeypatch):
        monkeypatch.setattr(scheduler, "AGENT_DB_PATH", db)
        woken: list[str] = []
        monkeypatch.setattr(scheduler, "_continue", lambda cid, notes, resume, **_: woken.append(cid))
        conversations.start(db, "hi")

        scheduler.fire_due("2030-01-01T00:00:00+00:00", _resume)

        assert woken == []

    def test_a_fired_reminder_is_retired(self, db: Path, monkeypatch):
        """Fires once, whether or not the continuation worked. One that keeps failing is a
        bug to find in the traceback, not a reason to retry it forever."""
        monkeypatch.setattr(scheduler, "AGENT_DB_PATH", db)
        monkeypatch.setattr(scheduler, "_continue", lambda cid, notes, resume, **_: None)
        conv = conversations.start(db, "hi")["id"]
        repo.reminders.add_reminder(db, "2020-01-01T00:00:00+00:00", "check it", conversation_id=conv)

        scheduler.fire_due("2030-01-01T00:00:00+00:00", _resume)

        assert repo.reminders.due_reminders(db, "2030-01-01T00:00:00+00:00") == []

    def test_a_continuation_that_throws_does_not_stop_the_others(self, db: Path, monkeypatch):
        """Every other conversation waiting on a reminder still gets its turn."""
        monkeypatch.setattr(scheduler, "AGENT_DB_PATH", db)
        reached: list[str] = []

        def explode(cid, notes, resume, **_):
            reached.append(cid)
            raise RuntimeError("provider is down")

        monkeypatch.setattr(scheduler, "_continue", explode)
        first = conversations.start(db, "one")["id"]
        second = conversations.start(db, "two")["id"]
        repo.reminders.add_reminder(db, "2020-01-01T00:00:00+00:00", "a", conversation_id=first)
        repo.reminders.add_reminder(db, "2020-01-01T00:00:00+00:00", "b", conversation_id=second)

        scheduler.fire_due("2030-01-01T00:00:00+00:00", _resume)

        assert sorted(reached) == sorted([first, second])


class TestTheTimerDoesNotStartOnImport:
    def test_importing_the_module_starts_nothing(self):
        """Importing must never start a thread — see `test_the_suite_does_not_start_loops`.
        The hazard the loop introduced outlives the loop, because this has a timer too."""
        assert scheduler._thread is None
