"""An append-only log of what he did and thought."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, select

from kith.infra.db.engine import as_dict, changed, session
from kith.infra.db.models import JournalEntry
from kith.infra.db.support import utc_now_iso


def add_journal(path: Path, entry: str) -> dict:
    with session(path) as db:
        row = JournalEntry(entry=entry, created_at=utc_now_iso())
        db.add(row)
        db.flush()  # assigns the id without ending the transaction
        return as_dict(row)


def list_journal(path: Path, limit: int = 50) -> list[dict]:
    with session(path) as db:
        rows = db.scalars(select(JournalEntry).order_by(JournalEntry.id.desc()).limit(limit)).all()
        return [as_dict(row) for row in rows]


def delete_journal(path: Path, entry_id: int) -> bool:
    with session(path) as db:
        return changed(db.execute(delete(JournalEntry).where(JournalEntry.id == entry_id))) > 0
