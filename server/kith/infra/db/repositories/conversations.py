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


# --------------------------------------------------------------------------- #
# A conversation as the unit of work
# --------------------------------------------------------------------------- #
#
# Two things a session carries beyond its words: what it is working on, and whether it keeps
# going. Both used to be global, and both were wrong for the same reason. The project came
# from "the only active project with a folder" — a guess that breaks the moment there are
# two, and two at once is the entire point of sessions. Continuing came from one roam switch
# over one board, which could only be on for everything or off for everything.


def set_project(path: Path, conversation_id: str, project_id: int | None) -> None:
    """Bind a session to the project it is working on, or unbind it."""
    with session(path) as db:
        row = db.get(Conversation, conversation_id)
        if row is not None:
            row.project_id = int(project_id) if project_id else None
            row.updated_at = utc_now_iso()


def project_of(path: Path, conversation_id: str) -> int | None:
    with session(path) as db:
        row = db.get(Conversation, conversation_id)
        return int(row.project_id) if row is not None and row.project_id else None


def set_working(path: Path, conversation_id: str, working: bool) -> None:
    """Whether he takes the next step here without being asked again.

    A property of the conversation rather than of the process, which is the whole difference
    from roaming: two sessions can be working at once, and stopping one must not stop the
    other.
    """
    with session(path) as db:
        row = db.get(Conversation, conversation_id)
        if row is not None:
            row.working = 1 if working else 0
            row.updated_at = utc_now_iso()


def is_working(path: Path, conversation_id: str) -> bool:
    with session(path) as db:
        row = db.get(Conversation, conversation_id)
        return bool(row is not None and row.working)


def working_sessions(path: Path) -> list[dict]:
    """Every session that should take another step, longest-waiting first.

    Ordered by when each was last touched so a busy session cannot starve the others simply
    by being busy.
    """
    with session(path) as db:
        rows = db.scalars(
            select(Conversation).where(Conversation.working == 1).order_by(Conversation.updated_at)
        ).all()
        return [{"id": r.id, "title": r.title, "project_id": r.project_id} for r in rows]
