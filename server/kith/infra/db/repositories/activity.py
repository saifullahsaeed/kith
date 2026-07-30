"""When he last did something of his own accord."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select, union_all

from kith.infra.db.engine import session
from kith.infra.db.models import JournalEntry, Memory


def last_activity_at(path: Path) -> str | None:
    """The most recent moment Kith did something himself (journaled or remembered).

    Two MAX values combined, rather than a join or a scan of either table: the
    question is only "when was the latest of these", and both columns are indexed.
    """
    latest = union_all(
        select(func.max(JournalEntry.created_at).label("at")),
        select(func.max(Memory.created_at).label("at")),
    ).subquery()

    with session(path) as db:
        value = db.scalar(select(func.max(latest.c.at)))
    return value or None
