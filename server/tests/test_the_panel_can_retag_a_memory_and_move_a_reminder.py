"""What the redesigned Control Panel edits that the panel before it could not.

The Memory screen gives a memory editable tags and an importance stepper, and the Reminders
screen gives a pending reminder Snooze and Edit. The generic `PATCH /api/brain/<kind>/<id>` took
neither: a memory's edit wrote content and importance only, so a tag change saved nothing and
said nothing; a reminder's edit could only set its status — and an empty body marked it done.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kith.infra.db import repositories as repo
from kith.services import brain


@pytest.fixture(autouse=True)
def no_embedder(monkeypatch):
    from kith.services import embeddings

    monkeypatch.setattr(embeddings, "embed", lambda text: None)


class TestRetaggingAMemory:
    def test_the_tags_are_replaced_as_a_set(self, db):
        made = brain.create(db, "memory", {"content": "he likes short answers", "tags": ["style"]})
        brain.update(db, "memory", made["id"], {"tags": ["style", "#preference", " ", "style"]})
        [row] = [m for m in repo.memories.list_memories(db) if m["id"] == made["id"]]
        assert row["tags"] == ["style", "preference"]

    def test_an_edit_without_tags_leaves_them_alone(self, db):
        made = brain.create(db, "memory", {"content": "x", "tags": ["kept"]})
        brain.update(db, "memory", made["id"], {"importance": 4})
        [row] = [m for m in repo.memories.list_memories(db) if m["id"] == made["id"]]
        assert row["tags"] == ["kept"]
        assert row["importance"] == 4

    def test_importance_stays_on_the_ten_point_scale(self, db):
        made = brain.create(db, "memory", {"content": "x"})
        assert brain.update(db, "memory", made["id"], {"importance": 40})["importance"] == 10
        assert brain.update(db, "memory", made["id"], {"importance": -3})["importance"] == 0

    def test_tags_that_are_not_a_list_are_refused(self, db):
        made = brain.create(db, "memory", {"content": "x"})
        with pytest.raises(ValueError):
            brain.update(db, "memory", made["id"], {"tags": "one,two"})


class TestMovingAReminder:
    def _pending(self, db) -> dict:
        return brain.create(db, "reminder", {"note": "check the build", "in_minutes": 10})

    def test_a_snooze_moves_it_later(self, db):
        made = self._pending(db)
        before = datetime.fromisoformat(made["fire_at"])
        moved = brain.update(db, "reminder", made["id"], {"in_minutes": 40})
        after = datetime.fromisoformat(moved["fire_at"])
        assert after - before > timedelta(minutes=25)
        assert moved["status"] == "pending"
        assert moved["fires"]

    def test_an_edit_can_reword_it_and_give_it_an_absolute_time(self, db):
        made = self._pending(db)
        at = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0)
        moved = brain.update(db, "reminder", made["id"], {"note": "  check CI  ", "at": at.isoformat()})
        assert moved["note"] == "check CI"
        assert datetime.fromisoformat(moved["fire_at"]) == at

    def test_cancelling_is_still_a_status_edit(self, db):
        made = self._pending(db)
        assert brain.update(db, "reminder", made["id"], {"status": "cancelled"})["status"] == "cancelled"

    def test_an_empty_edit_no_longer_marks_it_done(self, db):
        """The old edit defaulted a missing status to "done" — a PATCH with nothing in it fired
        the reminder's bookkeeping without the reminder ever firing."""
        made = self._pending(db)
        assert brain.update(db, "reminder", made["id"], {})["status"] == "pending"

    def test_a_fired_reminder_cannot_be_moved(self, db):
        made = self._pending(db)
        brain.update(db, "reminder", made["id"], {"status": "done"})
        with pytest.raises(ValueError):
            brain.update(db, "reminder", made["id"], {"in_minutes": 30})
