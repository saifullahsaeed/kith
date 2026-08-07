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


def set_project(path: Path, conversation_id: str, project_id: int | None) -> bool:
    """Bind a session to the project it is working on, or unbind it — once.

    Refuses once a project is already set and a *different* value (including null, an
    unbind) is asked for: a conversation that has picked a project is stuck with it for the
    rest of its life, the same way it is stuck with whatever it has already said. Re-setting
    the same value is a harmless no-op, and the first bind of an unbound conversation always
    succeeds. Returns whether the requested value now actually holds, so a caller that needs
    to tell "bound" apart from "refused, already bound elsewhere" can — `adopt()` does not
    need to (it is bookkeeping and silent either way); the explicit `/conversations/.../project`
    route does, so the person gets a real answer instead of a click that quietly did nothing.
    """
    with session(path) as db:
        row = db.get(Conversation, conversation_id)
        if row is None:
            return False
        current = int(row.project_id) if row.project_id else None
        wanted = int(project_id) if project_id else None
        if current is not None and current != wanted:
            return False
        if current == wanted:
            return True
        row.project_id = wanted
        row.updated_at = utc_now_iso()
        return True


def project_of(path: Path, conversation_id: str) -> int | None:
    with session(path) as db:
        row = db.get(Conversation, conversation_id)
        return int(row.project_id) if row is not None and row.project_id else None


def session_for_project(path: Path, project_id: int | None) -> str:
    """The session driving this project, or the most recent session if none is bound.

    Used to decide who should hear about something that happened to a task. With two projects
    going, "wake a session" is not good enough — the one that should answer is the one working
    that project, and waking the other means the answer arrives in the wrong conversation
    against the wrong folder.

    Falls back to the newest conversation, because a task with no project still belongs to
    whoever is here, and answering in the last place someone was talking beats answering
    nowhere.
    """
    with session(path) as db:
        if project_id:
            row = db.scalars(
                select(Conversation)
                .where(Conversation.project_id == int(project_id))
                .order_by(Conversation.updated_at.desc())
                .limit(1)
            ).first()
            if row is not None:
                return str(row.id)
        newest = db.scalars(select(Conversation).order_by(Conversation.updated_at.desc()).limit(1)).first()
        return str(newest.id) if newest is not None else ""


