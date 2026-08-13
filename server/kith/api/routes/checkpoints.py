"""Checkpoints, as a person acts on them — listing, and restoring.

A new resource, not an extension of `workspace.py`'s routes (which only browse files,
never revert them) or `conversations.py`'s (checkpoints are git/filesystem state, not
conversation metadata). There is no route here an AI tool calls — restoring is a person's
decision, made by clicking, never Kith's.
"""

from __future__ import annotations

from flask import jsonify

from kith.api.blueprint import api
from kith.infra import workspace
from kith.services import checkpoints
from kith.settings import AGENT_DB_PATH


@api.get("/conversations/<conversation_id>/checkpoints")
@api.doc(
    summary="Checkpoints made during a conversation",
    description="One per turn that actually changed a file, tagged with the turn it "
    "happened during so the UI can match it to a message by index.",
)
def list_checkpoints(conversation_id: str):
    return jsonify({"checkpoints": checkpoints.for_conversation(AGENT_DB_PATH, conversation_id)})


@api.post("/checkpoints/<int:checkpoint_id>/restore")
@api.doc(
    summary="Revert files to a checkpoint",
    description="Makes the repo's working tree and index match this checkpoint exactly. "
    "Takes one more checkpoint of the current state first, so this is itself reversible.",
)
def restore_checkpoint(checkpoint_id: int):
    try:
        return jsonify(checkpoints.restore(AGENT_DB_PATH, checkpoint_id))
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    except workspace.WorkspaceError as exc:
        return jsonify({"error": str(exc)}), 409
