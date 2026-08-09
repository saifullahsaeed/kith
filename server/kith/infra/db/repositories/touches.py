"""Which files a conversation has opened or changed.

One row per (conversation, path): a touch replaces the previous one rather than appending,
so the table answers "what state does he believe this file to be in" and stays the size of
the file set rather than the touch count.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from kith.infra.db.engine import as_dict, session
from kith.infra.db.models import FileTouch
from kith.infra.db.support import utc_now_iso


def touch(path: Path, conversation_id: str, file_path: str, action: str, version: str) -> dict:
    """Record the latest touch of one file, replacing any earlier one."""
    with session(path) as db:
        row = db.scalar(
            select(FileTouch).where(
                FileTouch.conversation_id == conversation_id,
                FileTouch.path == file_path,
            )
        )
        if row is None:
            row = FileTouch(conversation_id=conversation_id, path=file_path)
            db.add(row)
        row.action = action
        row.version = version
        row.at = utc_now_iso()
        db.flush()
        return as_dict(row)


def touched_files(path: Path, conversation_id: str) -> list[dict]:
    """Every file this conversation has touched, oldest touch first."""
    with session(path) as db:
        rows = db.scalars(
            select(FileTouch)
            .where(FileTouch.conversation_id == conversation_id)
            .order_by(FileTouch.at, FileTouch.id)
        ).all()
        return [as_dict(row) for row in rows]
