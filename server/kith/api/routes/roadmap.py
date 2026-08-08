"""A project's roadmap as a graph — and what it lets him do next.

The graph is not a picture of the work; it decides the work. A milestone whose predecessors
are unfinished holds its own tasks back, so editing an edge here changes what he picks up on
his next turn. That is the whole reason these endpoints exist rather than the roadmap being
a read-only rendering of the milestone list.
"""

from __future__ import annotations

from flask import jsonify, request

from kith.api.blueprint import api
from kith.config import AGENT_DB_PATH
from kith.infra.db import repositories as repo


@api.get("/projects/<int:project_id>/roadmap")
@api.doc(
    summary="One project's milestones as a graph",
    description=(
        "Nodes carry `ready` (nothing in the way, not yet done) and `blocked_by` (the "
        "titles of what it is waiting for, so the reason can be shown rather than just the "
        "state), plus task counts and any hand-placed position."
    ),
)
def get_roadmap(project_id: int):
    return jsonify(repo.projects.roadmap(AGENT_DB_PATH, project_id))


@api.post("/projects/<int:project_id>/roadmap/dependencies")
@api.doc(
    summary="Make one milestone wait for another",
    description="Refuses a cycle: a loop is a roadmap where nothing is ever available.",
)
def add_dependency(project_id: int):
    payload = request.get_json(silent=True) or {}
    try:
        repo.projects.add_dependency(
            AGENT_DB_PATH,
            int(payload.get("milestoneId")),
            int(payload.get("dependsOnId")),
        )
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc) or "milestoneId and dependsOnId are required"}), 400
    return jsonify(repo.projects.roadmap(AGENT_DB_PATH, project_id))


@api.delete("/projects/<int:project_id>/roadmap/dependencies")
@api.doc(summary="Stop one milestone waiting for another")
def remove_dependency(project_id: int):
    try:
        milestone_id = int(request.args["milestoneId"])
        depends_on_id = int(request.args["dependsOnId"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "milestoneId and dependsOnId are required"}), 400
    repo.projects.remove_dependency(AGENT_DB_PATH, milestone_id, depends_on_id)
    return jsonify(repo.projects.roadmap(AGENT_DB_PATH, project_id))


@api.put("/projects/<int:project_id>/roadmap/positions")
@api.doc(
    summary="Remember where the nodes were dragged to",
    description=(
        "Sent as one batch after a drag settles rather than per frame — a request per "
        "pointer move would be hundreds of writes to say one thing."
    ),
)
def save_positions(project_id: int):
    payload = request.get_json(silent=True) or {}
    for node in payload.get("positions") or []:
        try:
            repo.projects.set_milestone_position(
                AGENT_DB_PATH, int(node["id"]), float(node["x"]), float(node["y"])
            )
        except (KeyError, TypeError, ValueError):
            continue
    return jsonify(repo.projects.roadmap(AGENT_DB_PATH, project_id))
