"""His freeform notebook."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, select

from kith.infra.db.engine import as_dict, session
from kith.infra.db.models import Note
from kith.infra.db.support import keyword_search, row_to_dict, utc_now_iso


def add_note(path: Path, title: str, body: str = "") -> dict:
    now = utc_now_iso()
    with session(path) as db:
        row = Note(title=title, body=body, created_at=now, updated_at=now)
        db.add(row)
        db.flush()
        return as_dict(row)


def list_notes(path: Path, limit: int = 50) -> list[dict]:
    with session(path) as db:
        rows = db.scalars(select(Note).order_by(Note.updated_at.desc()).limit(limit)).all()
        return [as_dict(row) for row in rows]


def search_notes(path: Path, query: str, limit: int = 20) -> list[dict]:
    # Still the hand-rolled keyword ranker: it scores by how many query tokens a
    # row matches, which is not something an ORM query expresses more clearly.
    return [row_to_dict(row) for row in keyword_search(path, "notes", ("title", "body"), query, limit)]


def update_note(path: Path, note_id: int, title: str | None = None, body: str | None = None) -> dict | None:
    with session(path) as db:
        row = db.get(Note, note_id)
        if row is None:
            return None
        if title is not None:
            row.title = title
        if body is not None:
            row.body = body
        row.updated_at = utc_now_iso()
        db.flush()
        return as_dict(row)


def delete_note(path: Path, note_id: int) -> bool:
    with session(path) as db:
        return db.execute(delete(Note).where(Note.id == note_id)).rowcount > 0
