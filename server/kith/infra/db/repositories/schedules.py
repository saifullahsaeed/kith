"""Standing jobs that fire on a repeat, not once."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, select

from kith.domain.enums import SCHEDULE_STATUSES
from kith.infra.db.engine import as_dict, session
from kith.infra.db.models import Schedule
from kith.infra.db.support import utc_now_iso


def add_schedule(
    path: Path, note: str, next_fire: str, every_minutes: int | None = None, daily_at: str | None = None
) -> dict:
    with session(path) as db:
        row = Schedule(
            note=note,
            every_minutes=every_minutes,
            daily_at=daily_at,
            next_fire=next_fire,
            status="active",
            created_at=utc_now_iso(),
        )
        db.add(row)
        db.flush()
        return as_dict(row)


def list_schedules(path: Path) -> list[dict]:
    with session(path) as db:
        rows = db.scalars(select(Schedule).order_by(Schedule.next_fire.asc())).all()
        return [as_dict(row) for row in rows]


def due_schedules(path: Path, now_iso: str) -> list[dict]:
    query = (
        select(Schedule)
        .where(Schedule.status == "active", Schedule.next_fire <= now_iso)
        .order_by(Schedule.next_fire.asc())
    )
    with session(path) as db:
        return [as_dict(row) for row in db.scalars(query).all()]


def reschedule(path: Path, schedule_id: int, next_fire: str) -> dict | None:
    with session(path) as db:
        row = db.get(Schedule, schedule_id)
        if row is None:
            return None
        row.next_fire = next_fire
        row.last_fired = utc_now_iso()
        db.flush()
        return as_dict(row)


def set_schedule_status(path: Path, schedule_id: int, status: str) -> dict | None:
    if status not in SCHEDULE_STATUSES:
        raise ValueError(f"status must be one of {SCHEDULE_STATUSES}")
    with session(path) as db:
        row = db.get(Schedule, schedule_id)
        if row is None:
            return None
        row.status = status
        db.flush()
        return as_dict(row)


def delete_schedule(path: Path, schedule_id: int) -> bool:
    with session(path) as db:
        return db.execute(delete(Schedule).where(Schedule.id == schedule_id)).rowcount > 0
