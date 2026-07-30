"""The people he knows and what he's noticed about them."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, select

from kith.infra.db.engine import as_dict, session
from kith.infra.db.models import Person
from kith.infra.db.support import utc_now_iso


def list_people(path: Path) -> list[dict]:
    with session(path) as db:
        rows = db.scalars(select(Person).order_by(Person.updated_at.desc())).all()
        return [as_dict(row) for row in rows]


def get_person(path: Path, name: str) -> dict | None:
    with session(path) as db:
        row = _by_name(db, name)
        return as_dict(row) if row else None


def upsert_person(path: Path, name: str, relationship: str | None = None, profile: str | None = None) -> dict:
    """Create them, or fill in only the fields actually supplied.

    Was an ``ON CONFLICT ... COALESCE`` upsert; expressed here as read-then-write,
    which reads better and behaves identically — the column is UNIQUE COLLATE
    NOCASE, so "saif" and "Saif" resolve to the same person either way.
    """
    now = utc_now_iso()
    with session(path) as db:
        row = _by_name(db, name)
        if row is None:
            row = Person(
                name=name,
                relationship=relationship or "",
                profile=profile or "",
                created_at=now,
                updated_at=now,
            )
            db.add(row)
        else:
            if relationship is not None:
                row.relationship = relationship
            if profile is not None:
                row.profile = profile
            row.updated_at = now
        db.flush()
        return as_dict(row)


def append_person_note(path: Path, name: str, note: str) -> dict:
    """Add a line to what Kith knows about someone, creating them if new."""
    existing = get_person(path, name)
    profile = (existing["profile"] + "\n" if existing and existing["profile"] else "") + f"- {note.strip()}"
    return upsert_person(path, name, profile=profile.strip())


def update_person(
    path: Path, person_id: int, relationship: str | None = None, profile: str | None = None
) -> dict | None:
    with session(path) as db:
        row = db.get(Person, person_id)
        if row is None:
            return None
        if relationship is not None:
            row.relationship = relationship
        if profile is not None:
            row.profile = profile
        row.updated_at = utc_now_iso()
        db.flush()
        return as_dict(row)


def delete_person(path: Path, person_id: int) -> bool:
    with session(path) as db:
        return db.execute(delete(Person).where(Person.id == person_id)).rowcount > 0


def _by_name(db, name: str) -> Person | None:
    # The column carries COLLATE NOCASE, so plain equality is already case-insensitive.
    return db.scalars(select(Person).where(Person.name == name)).first()
