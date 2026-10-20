"""What the sessions of an agent tree say to each other.

One table, written by `services/agents` and read by nothing else. A row is undelivered until the
recipient's turn takes it into its transcript; `take` is the only thing that delivers, and it does
it in the same transaction it reads in, so two turns racing for the same mail cannot both get it.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from kith.infra.db.engine import as_dict, session
from kith.infra.db.models import AgentMail
from kith.infra.db.support import utc_now_iso


def post(path: Path, to_id: str, from_id: str, kind: str, body: str) -> dict:
    with session(path) as db:
        row = AgentMail(to_id=to_id, from_id=from_id, kind=kind, body=body, created_at=utc_now_iso())
        db.add(row)
        db.flush()
        return as_dict(row)


def waiting(path: Path, to_id: str) -> list[dict]:
    """Undelivered mail for one conversation, oldest first. Reads; does not deliver."""
    with session(path) as db:
        rows = db.scalars(
            select(AgentMail)
            .where(AgentMail.to_id == to_id, AgentMail.delivered_at.is_(None))
            .order_by(AgentMail.id)
        ).all()
        return [as_dict(row) for row in rows]


def take(path: Path, to_id: str) -> list[dict]:
    """Undelivered mail for one conversation, oldest first, marked delivered as it is read."""
    with session(path) as db:
        rows = db.scalars(
            select(AgentMail)
            .where(AgentMail.to_id == to_id, AgentMail.delivered_at.is_(None))
            .order_by(AgentMail.id)
        ).all()
        now = utc_now_iso()
        for row in rows:
            row.delivered_at = now
        return [as_dict(row) for row in rows]


def delivered(path: Path, to_id: str, *, kind: str = "", limit: int = 20) -> list[dict]:
    """Mail a conversation has been handed, oldest first — optionally only one kind."""
    with session(path) as db:
        query = select(AgentMail).where(AgentMail.to_id == to_id, AgentMail.delivered_at.is_not(None))
        if kind:
            query = query.where(AgentMail.kind == kind)
        rows = db.scalars(query.order_by(AgentMail.id).limit(max(1, limit))).all()
        return [as_dict(row) for row in rows]


def addressees(path: Path) -> list[str]:
    """Every conversation with mail it has not been handed yet."""
    with session(path) as db:
        rows = db.scalars(select(AgentMail.to_id).where(AgentMail.delivered_at.is_(None)).distinct()).all()
        return [str(row) for row in rows]
