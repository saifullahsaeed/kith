"""His mind: the whole snapshot, his lifetime, and generic edits by kind."""

from __future__ import annotations

from flask import jsonify, request

from kith.api.blueprint import api
from kith.infra.db import repositories as repo
from kith.services import brain
from kith.settings import AGENT_DB_PATH


@api.get("/brain")
@api.doc(
    summary="Kith's whole database",
    description="Snapshot of memories, notes, journal, tasks, and self-made tools.",
)
def brain_snapshot():
    return jsonify(brain.snapshot(AGENT_DB_PATH))


@api.get("/projects")
@api.doc(
    summary="Just the projects",
    description="Names, statuses and folders — what a list of projects needs and nothing else.",
)
def projects():
    """The cheap answer to "what projects are there".

    `/api/brain` is the whole database — 303KB of it here, of which 204KB is 589 journal entries
    and 75KB is 94 tasks. The conversations panel groups its rows by project, so it needs names and
    statuses: 5.5KB, 1.8% of what it was being sent.

    That was tolerable while the panel fetched it once. It stopped being tolerable when `task`
    became a change that invalidates the snapshot: a turn ticking checklist items now re-sent a
    third of a megabyte, for the browser to parse and throw away, once per tick — to redraw four
    project names.
    """
    return jsonify({"projects": repo.projects.list_projects(AGENT_DB_PATH)})


@api.get("/schedules")
@api.doc(
    summary="Just the standing jobs",
    description="What repeats, when it next fires, and which chat it wakes.",
)
def schedules():
    """The same cheap answer as `/projects`, for the Work panel.

    The Standing section reads `schedules` — an array that is usually empty and here holds one row
    — and was reading it out of `/api/brain`, which is 303KB of journal and tasks. That panel is
    open the whole time, so it held the whole database in cache and refetched it on every `task`
    change, to redraw a countdown.
    """
    return jsonify({"schedules": repo.schedules.list_schedules(AGENT_DB_PATH)})


@api.get("/brain/timeline")
@api.doc(summary="Kith's lifetime", description="Everything that has happened, merged newest-first.")
def brain_timeline():
    return jsonify(brain.timeline(AGENT_DB_PATH))


@api.post("/brain/<kind>")
@api.doc(summary="Add an item", description="kind = memory | note | task.")
def brain_create(kind):
    try:
        return jsonify(brain.create(AGENT_DB_PATH, kind, request.get_json(silent=True) or {}))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@api.patch("/brain/<kind>/<key>")
@api.doc(summary="Edit an item", description="kind = memory | note | task.")
def brain_update(kind, key):
    try:
        return jsonify(brain.update(AGENT_DB_PATH, kind, key, request.get_json(silent=True) or {}) or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@api.delete("/brain/<kind>/<key>")
@api.doc(summary="Delete an item", description="kind = memory | note | journal | task | tool.")
def brain_delete(kind, key):
    try:
        return jsonify({"deleted": brain.delete(AGENT_DB_PATH, kind, key)})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@api.post("/brain/memory/<int:memory_id>/level")
@api.doc(summary="Set a memory's level", description="Move a memory to 'core' (front of mind) or 'recall'.")
def brain_set_memory_level(memory_id):
    body = request.get_json(silent=True) or {}
    try:
        updated = repo.memories.set_memory_level(AGENT_DB_PATH, memory_id, body.get("level", "recall"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(updated or {})
