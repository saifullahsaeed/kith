"""Workflow runs, for the Work panel: what is going, and the switch that stops one.

Starting is his, through `run_workflow`, and only when a message asked for it. Stopping is
yours — a fan-out of agents spending money in the background is exactly the thing a person must
be able to end from outside the conversation, and it has no turn of its own to press Stop on.
"""

from __future__ import annotations

from flask import jsonify, request

from kith.api.blueprint import api
from kith.services import workflows


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
