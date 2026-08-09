"""Which files a conversation has opened or changed.

One row per (conversation, path): a touch replaces the previous one rather than appending,
so the table answers "what state does he believe this file to be in" and stays the size of
the file set rather than the touch count.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select

from kith.infra.db.engine import as_dict, session
from kith.infra.db.models import FileTouch
from kith.infra.db.support import utc_now_iso


def touch(
    path: Path,
    conversation_id: str,
    file_path: str,
    action: str,
    version: str,
    extent: str = "",
) -> dict:
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
        row.extent = extent
        row.at = utc_now_iso()
        db.flush()
        return as_dict(row)


def touched_files(path: Path, conversation_id: str, limit: int | None = None) -> list[dict]:
    """Files this conversation has touched, oldest touch first.

    ``limit`` keeps the *newest* that many — what he touched recently is what he is working
    on — while the returned order stays oldest-first so it reads as the session in sequence.
    It matters more than it looks: every row the caller gets is a `stat` on the prompt-
    assembly path, so loading four hundred to display forty would be four hundred syscalls
    a turn to throw away nine tenths of them.
    """
    with session(path) as db:
        query = select(FileTouch).where(FileTouch.conversation_id == conversation_id)
        if limit is None:
            rows = db.scalars(query.order_by(FileTouch.at, FileTouch.id)).all()
        else:
            newest = db.scalars(query.order_by(FileTouch.at.desc(), FileTouch.id.desc()).limit(limit)).all()
            rows = list(reversed(newest))
        return [as_dict(row) for row in rows]


def count_touched(path: Path, conversation_id: str) -> int:
    """How many files in total, so a capped manifest can say what it is not showing."""
    with session(path) as db:
        return int(
            db.scalar(
                select(func.count())
                .select_from(FileTouch)
                .where(FileTouch.conversation_id == conversation_id)
            )
            or 0
        )


def forget_conversation(path: Path, conversation_id: str) -> None:
    """Drop every row for a conversation that no longer exists.

    Deleting a conversation removes its index row and nothing else, so without this these
    rows outlive the thing they describe — forever, and invisibly, since no manifest will
    ever ask for them again.
    """
    with session(path) as db:
        db.execute(sql_delete(FileTouch).where(FileTouch.conversation_id == conversation_id))
