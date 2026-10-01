"""One-off nudges at a time — his sense of when."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, select

from kith.domain.enums import REMINDER_STATUSES
from kith.infra.db.engine import as_dict, changed, session
from kith.infra.db.models import Reminder
from kith.infra.db.support import utc_now_iso


def add_reminder(path: Path, fire_at: str, note: str, conversation_id: str = "") -> dict:
    with session(path) as db:
        row = Reminder(
            fire_at=fire_at,
            note=note,
            status="pending",
            created_at=utc_now_iso(),
            conversation_id=conversation_id or None,
        )
        db.add(row)
        db.flush()
        return as_dict(row)


def list_reminders(path: Path, status: str | None = None) -> list[dict]:
    query = select(Reminder).order_by(Reminder.fire_at.asc())
    if status:
        query = query.where(Reminder.status == status)
    with session(path) as db:
        return [as_dict(row) for row in db.scalars(query).all()]


def due_reminders(path: Path, now_iso: str) -> list[dict]:
    """Pending reminders whose time has arrived, oldest first."""
    query = (
        select(Reminder)
        .where(Reminder.status == "pending", Reminder.fire_at <= now_iso)
        .order_by(Reminder.fire_at.asc())
    )
    with session(path) as db:
        return [as_dict(row) for row in db.scalars(query).all()]


def set_reminder_status(path: Path, reminder_id: int, status: str) -> dict | None:
    if status not in REMINDER_STATUSES:
        raise ValueError(f"status must be one of {REMINDER_STATUSES}")
    with session(path) as db:
        row = db.get(Reminder, reminder_id)
        if row is None:
            return None
        row.status = status
        db.flush()
        return as_dict(row)


def update_reminder(
    path: Path,
    reminder_id: int,
    *,
    status: str | None = None,
    note: str | None = None,
    fire_at: str | None = None,
) -> dict | None:
    """Change a reminder's words, its time, or its status — one write, so a snooze cannot land half.

    Only a pending reminder can be moved or reworded: one that has fired already did its job, and
    rewriting it would make the alerts log disagree with what actually reached him. "Remind again"
    is a new reminder, not an edit of the old one.
    """
    if status is not None and status not in REMINDER_STATUSES:
        raise ValueError(f"status must be one of {REMINDER_STATUSES}")
    with session(path) as db:
        row = db.get(Reminder, reminder_id)
        if row is None:
            return None
        if (note is not None or fire_at is not None) and row.status != "pending":
            raise ValueError("only a pending reminder can be moved or reworded")
        if note is not None:
            if not note.strip():
                raise ValueError("note is required")
            row.note = note.strip()
        if fire_at is not None:
            row.fire_at = fire_at
        if status is not None:
            row.status = status
        db.flush()
        return as_dict(row)


def attach_conversation(path: Path, reminder_id: int, conversation_id: str) -> None:
    """Give a reminder that belonged to no chat the one it fired into."""
    with session(path) as db:
        row = db.get(Reminder, reminder_id)
        if row is not None:
            row.conversation_id = conversation_id
            db.flush()


def delete_reminder(path: Path, reminder_id: int) -> bool:
    with session(path) as db:
        return changed(db.execute(delete(Reminder).where(Reminder.id == reminder_id))) > 0
