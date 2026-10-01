"""An append-only log of what he did and thought."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, select

from kith.infra.db.engine import as_dict, changed, session
from kith.infra.db.models import JournalEntry
from kith.infra.db.support import created_before, span_of, utc_now_iso
from kith.kernel import session_context


def add_journal(path: Path, entry: str, project_id: int | None = None) -> dict:
    """One line about what happened, scoped to the work it happened inside.

    The project defaults to the session's rather than being threaded through every caller: the
    journal is written from deep inside a turn, by the harness as often as by him, and a
    parameter every one of those had to remember would be a parameter most of them forgot.
    """
    with session(path) as db:
        row = JournalEntry(
            entry=entry,
            created_at=utc_now_iso(),
            project_id=project_id if project_id is not None else session_context.current_project(),
        )
        db.add(row)
        db.flush()  # assigns the id without ending the transaction
        return as_dict(row)


def list_journal(path: Path, limit: int = 50) -> list[dict]:
    with session(path) as db:
        rows = db.scalars(select(JournalEntry).order_by(JournalEntry.id.desc()).limit(limit)).all()
        return [as_dict(row) for row in rows]


def journal_before(path: Path, before: str | None, limit: int) -> list[dict]:
    """A page of the lifetime's journal — see `support.created_before`."""
    with session(path) as db:
        return [as_dict(row) for row in db.scalars(created_before(JournalEntry, before, limit)).all()]


def journal_span(path: Path) -> tuple[int, str | None]:
    """How many there are, and when the first was written — see `support.span_of`."""
    with session(path) as db:
        count, first = db.execute(span_of(JournalEntry)).one()
        return int(count or 0), first


def delete_journal(path: Path, entry_id: int) -> bool:
    with session(path) as db:
        return changed(db.execute(delete(JournalEntry).where(JournalEntry.id == entry_id))) > 0
