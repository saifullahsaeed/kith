"""Things he's wondering about, and how far he's got with them."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, select

from kith.domain.enums import CURIOSITY_STATUSES
from kith.infra.db.engine import as_dict, session
from kith.infra.db.models import Curiosity
from kith.infra.db.support import utc_now_iso


def add_curiosity(path: Path, topic: str, note: str = "") -> dict:
    now = utc_now_iso()
    with session(path) as db:
        row = Curiosity(topic=topic, note=note, status="open", created_at=now, updated_at=now)
        db.add(row)
        db.flush()
        return as_dict(row)


def list_curiosities(path: Path, status: str | None = None) -> list[dict]:
    query = select(Curiosity).order_by(Curiosity.updated_at.desc())
    if status:
        query = query.where(Curiosity.status == status)
    with session(path) as db:
        return [as_dict(row) for row in db.scalars(query).all()]


def update_curiosity(
    path: Path, curiosity_id: int, status: str | None = None, note: str | None = None
) -> dict | None:
    if status is not None and status not in CURIOSITY_STATUSES:
        raise ValueError(f"status must be one of {CURIOSITY_STATUSES}")
    with session(path) as db:
        row = db.get(Curiosity, curiosity_id)
        if row is None:
            return None
        if status is not None:
            row.status = status
        if note is not None:
            row.note = note
        row.updated_at = utc_now_iso()
        db.flush()
        return as_dict(row)


def delete_curiosity(path: Path, curiosity_id: int) -> bool:
    with session(path) as db:
        return db.execute(delete(Curiosity).where(Curiosity.id == curiosity_id)).rowcount > 0
