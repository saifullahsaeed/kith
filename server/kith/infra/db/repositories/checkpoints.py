"""Bookkeeping for the checkpoint chain the UI browses and restores from.

The chain's own integrity lives entirely in git — see
:func:`kith.infra.workspace._take_checkpoint`. This table exists only so the UI can list a
conversation's checkpoints and correlate each with the turn it happened during without
walking every repo's ref history to do it.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from kith.infra.db.engine import as_dict, session
from kith.infra.db.models import Checkpoint
from kith.infra.db.support import utc_now_iso


def add_checkpoint(
    path: Path,
    repo_root: str,
    sha: str,
    tree_sha: str,
    parent_sha: str | None,
    conversation_id: str | None,
    trigger: str,
) -> dict:
    with session(path) as db:
        row = Checkpoint(
            repo_root=repo_root,
            sha=sha,
            tree_sha=tree_sha,
            parent_sha=parent_sha,
            conversation_id=conversation_id,
            trigger=trigger,
            created_at=utc_now_iso(),
        )
        db.add(row)
        db.flush()
        return as_dict(row)


def get_checkpoint(path: Path, checkpoint_id: int) -> dict | None:
    with session(path) as db:
        row = db.get(Checkpoint, checkpoint_id)
        return as_dict(row) if row is not None else None


def list_for_conversation(path: Path, conversation_id: str) -> list[dict]:
    """Oldest first — the order a turn-index correlation walks in."""
    with session(path) as db:
        rows = db.scalars(
            select(Checkpoint)
            .where(Checkpoint.conversation_id == conversation_id)
            .order_by(Checkpoint.id.asc())
        ).all()
        return [as_dict(row) for row in rows]
