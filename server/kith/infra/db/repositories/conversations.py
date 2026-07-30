"""The conversation index.

Only metadata lives here. The words themselves are append-only JSONL files in the
workspace (see :mod:`kith.services.conversations`), because a plain-text transcript
outlives this program and a table does not. This is the part that answers "list them,
newest first" without opening a hundred files.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete as sql_delete
from sqlalchemy import select

from kith.infra.db.engine import as_dict, session
from kith.infra.db.models import Conversation
from kith.infra.db.support import utc_now_iso


def create(path: Path, conversation_id: str, title: str, session_id: str) -> dict:
    now = utc_now_iso()
    with session(path) as db:
        row = Conversation(
            id=conversation_id,
            title=title,
            session_id=session_id,
            created_at=now,
            updated_at=now,
            messages=0,
        )
        db.add(row)
        db.flush()
        return as_dict(row)


def get(path: Path, conversation_id: str) -> dict | None:
    with session(path) as db:
        row = db.scalar(select(Conversation).where(Conversation.id == conversation_id))
        return as_dict(row) if row else None


def recent(path: Path, limit: int = 50) -> list[dict]:
    """Newest first — the order a sidebar wants and the only order anyone scrolls."""
    with session(path) as db:
        rows = db.scalars(
            select(Conversation).order_by(Conversation.updated_at.desc()).limit(max(1, limit))
        ).all()
        return [as_dict(row) for row in rows]


def touch(path: Path, conversation_id: str, delta: int = 0) -> None:
    """Mark it as the most recent, and count a message if one was added."""
    with session(path) as db:
        row = db.scalar(select(Conversation).where(Conversation.id == conversation_id))
        if row is None:
            return
        row.updated_at = utc_now_iso()
        if delta:
            row.messages = int(row.messages or 0) + delta


def rename(path: Path, conversation_id: str, title: str) -> None:
    with session(path) as db:
        row = db.scalar(select(Conversation).where(Conversation.id == conversation_id))
        if row is not None:
            row.title = title


def delete(path: Path, conversation_id: str) -> None:
    """Remove the index row. The transcript file is the caller's decision."""
    with session(path) as db:
        db.execute(sql_delete(Conversation).where(Conversation.id == conversation_id))
