"""Sub-agents that can be asked one more thing after they have reported.

A row is the whole worker — see :class:`~kith.infra.db.models.Worker`. This module is
deliberately thin: it stores and fetches, and every decision about *what a worker may do* is
in `tools/delegation.py`, where the tool sets that enforce it also live. A storage layer that
knew a scout from a builder would be a second place to keep that list correct.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from kith.infra.db.engine import as_dict, session
from kith.infra.db.models import Worker
from kith.infra.db.support import utc_now_iso

#: How many messages of a worker's private list are kept. A worker followed up five times has
#: read five times as much, and the row is re-sent to the provider in full on every resumed
#: round — so an uncapped scratchpad turns the thing that saves context into a thing that
#: quietly accumulates it somewhere nobody is looking.
#:
#: The oldest are dropped rather than the newest, and the first message is always kept: it is
#: the brief, and a worker that has forgotten what it was sent to do is worse than one that
#: has forgotten how it got here.
_SCRATCHPAD_LIMIT = 120


def start(
    path: Path,
    worker_id: str,
    conversation_id: str,
    role: str,
    objective: str,
    worktree: str = "",
) -> dict:
    """Record a worker that is about to run. Returns the row."""
    now = utc_now_iso()
    with session(path) as db:
        row = Worker(
            worker_id=str(worker_id),
            conversation_id=str(conversation_id or ""),
            role=str(role),
            objective=str(objective),
            worktree=str(worktree or ""),
            state="out",
            created_at=now,
            at=now,
        )
        db.add(row)
        db.flush()
        return as_dict(row)


def get(path: Path, worker_id: str) -> dict | None:
    with session(path) as db:
        row = db.scalar(select(Worker).where(Worker.worker_id == str(worker_id)))
        return as_dict(row) if row is not None else None


def scratchpad(path: Path, worker_id: str) -> list[dict]:
    """The worker's private message list, or [] when there is no such worker.

    Empty for an unparseable blob as well as a missing row, and on purpose: the caller is
    about to run more rounds, and starting from nothing is recoverable where raising is not.
    """
    row = get(path, worker_id)
    if row is None:
        return []
    try:
        loaded = json.loads(row.get("scratchpad") or "[]")
    except (TypeError, ValueError):
        return []
    return loaded if isinstance(loaded, list) else []


def reported(path: Path, worker_id: str, messages: list[dict], report: str, rounds: int) -> dict | None:
    """Store what the worker has said and everything it is carrying, and mark it askable."""
    trimmed = _trim(messages)
    with session(path) as db:
        row = db.scalar(select(Worker).where(Worker.worker_id == str(worker_id)))
        if row is None:
            return None
        row.scratchpad = json.dumps(trimmed)
        row.report = str(report or "")
        row.rounds = int(row.rounds or 0) + max(0, int(rounds))
        row.state = "reported"
        row.at = utc_now_iso()
        db.flush()
        return as_dict(row)


def spend(path: Path, worker_id: str) -> None:
    """Mark a worker as no longer resumable — its copy of the tree has been taken away."""
    with session(path) as db:
        row = db.scalar(select(Worker).where(Worker.worker_id == str(worker_id)))
        if row is None:
            return
        row.state = "spent"
        row.worktree = ""
        row.at = utc_now_iso()
        db.flush()


def resumable(path: Path, conversation_id: str, limit: int = 20) -> list[dict]:
    """Workers this conversation can still ask something of, most recent first."""
    with session(path) as db:
        rows = db.scalars(
            select(Worker)
            .where(
                Worker.conversation_id == str(conversation_id or ""),
                Worker.state == "reported",
            )
            .order_by(Worker.at.desc())
            .limit(max(1, int(limit)))
        ).all()
        return [as_dict(row) for row in rows]


def _trim(messages: list[dict]) -> list[dict]:
    """The scratchpad, capped, with the brief kept whatever else goes."""
    if len(messages) <= _SCRATCHPAD_LIMIT:
        return list(messages)
    return [messages[0], *messages[-(_SCRATCHPAD_LIMIT - 1) :]]
