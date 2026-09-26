"""Workflow runs, for the Work panel: what is going, and the switch that stops one.

Starting is his, through `run_workflow`, and only when a message asked for it. Stopping is
yours — a fan-out of agents spending money in the background is exactly the thing a person must
be able to end from outside the conversation, and it has no turn of its own to press Stop on.
"""

from __future__ import annotations

from flask import jsonify, request

from kith.api.blueprint import api
from kith.infra.db import repositories as repo
from kith.services import workflows
from kith.settings import AGENT_DB_PATH


@api.get("/workflows")
@api.doc(
    summary="Workflow runs",
    description="Runs this process knows about, newest first, optionally for one conversation.",
)
def list_workflows():
    return jsonify({"runs": workflows.listing(str(request.args.get("conversation") or ""))})


@api.post("/workflows/<run_id>/stop")
@api.doc(
    summary="Stop a workflow run",
    description=(
        "Ends the script and asks every agent to stop at its next event. The conversation is "
        "still woken with whatever finished. Returns whether there was a running one to stop."
    ),
)
def stop_workflow(run_id: str):
    return jsonify({"stopping": workflows.stop(run_id)})


@api.get("/chat/<conversation_id>/workflow-mode")
@api.doc(
    summary="Whether workflow mode is on in a conversation",
    description="On means he may start workflows without the /workflow prefix.",
)
def get_workflow_mode(conversation_id: str):
    return jsonify({"on": repo.conversations.workflow_mode_of(AGENT_DB_PATH, conversation_id)})


@api.post("/chat/<conversation_id>/workflow-mode")
@api.doc(
    summary="Turn workflow mode on or off in a conversation",
    description=(
        "Body {on: bool}. Takes effect from the next turn. The /workflow prefix keeps working "
        "either way; this lets him decide for himself when work is worth fanning out."
    ),
)
def set_workflow_mode(conversation_id: str):
    body = request.get_json(silent=True) or {}
    on = bool(body.get("on"))
    if not repo.conversations.set_workflow_mode(AGENT_DB_PATH, conversation_id, on):
        return jsonify({"error": "no such conversation"}), 404
    return jsonify({"on": on})
