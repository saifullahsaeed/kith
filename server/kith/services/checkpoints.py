"""Checkpoints, as a person sees them: which turn each one belongs to, and restoring.

Everything about *taking* a checkpoint lives in :mod:`kith.infra.workspace` — it happens
automatically, inside a turn, with no person or model involved. This module is the other
half: listing a conversation's checkpoints for the UI, and running a restore a person
asked for. There is no AI-callable path to either — restoring is a person's call, never
his.
"""

from __future__ import annotations

from pathlib import Path

from kith.infra import workspace
from kith.infra.db import repositories as repo
from kith.services import conversations


def for_conversation(agent_db_path: Path, conversation_id: str) -> list[dict]:
    """Every checkpoint made during this conversation, each tagged with the turn it
    happened during — the same index the UI already uses for its messages, so it can
    match one to the other with an integer comparison, not timestamp math."""
    rows = repo.checkpoints.list_for_conversation(agent_db_path, conversation_id)
    if not rows:
        return []

    turn_ats = [turn.get("at") or "" for turn in conversations.timeline(conversation_id)]

    out = []
    for row in rows:
        turn_index = _turn_index_at_or_before(turn_ats, row["created_at"])
        if turn_index is None:
            continue  # bookkeeping predates every turn the UI can show — nothing to attach it to
        out.append(
            {
                "id": row["id"],
                "sha": row["sha"],
                "parentSha": row.get("parent_sha"),
                "repoRoot": row["repo_root"],
                "trigger": row["trigger"],
                "createdAt": row["created_at"],
                "turnIndex": turn_index,
            }
        )
    return out


def _turn_index_at_or_before(turn_ats: list[str], created_at: str) -> int | None:
    """The highest turn index whose own start time is at or before `created_at`. ISO
    timestamps sort lexically, and a transcript is append-only, so `turn_ats` is already
    non-decreasing — once one entry is past `created_at`, nothing after it can be earlier."""
    found = None
    for index, at in enumerate(turn_ats):
        if at and at <= created_at:
            found = index
        else:
            break
    return found


def restore(agent_db_path: Path, checkpoint_id: int) -> dict:
    """Revert a repo's files to a checkpoint, and record the safety checkpoint
    `workspace.restore_to_sha` takes of the state right before doing it.

    That safety checkpoint is taken by the same machinery as any other, but with no turn
    open around it — so nothing writes it to the database automatically the way
    `_checkpoint_before_change` does. This is where it gets attached to the same
    conversation the checkpoint being restored belongs to, so it shows up in the same
    list and can itself be restored to.
    """
    checkpoint = repo.checkpoints.get_checkpoint(agent_db_path, checkpoint_id)
    if checkpoint is None:
        raise KeyError(f"no checkpoint #{checkpoint_id}")

    result = workspace.restore_to_sha(Path(checkpoint["repo_root"]), checkpoint["sha"])

    safety_id = None
    safety = result.get("safety")
    if safety:
        saved = repo.checkpoints.add_checkpoint(
            agent_db_path,
            repo_root=safety["repo_root"],
            sha=safety["sha"],
            tree_sha=safety["tree_sha"],
            parent_sha=safety["parent_sha"],
            conversation_id=checkpoint.get("conversation_id"),
            trigger="restore",
        )
        safety_id = saved["id"]

    return {"ok": True, "dirtyBefore": result["dirty_before"], "safetyCheckpointId": safety_id}
