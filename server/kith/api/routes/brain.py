"""His mind: the whole snapshot, his lifetime, and generic edits by kind."""

from __future__ import annotations

from datetime import UTC, datetime

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


@api.get("/projects/<int:project_id>/tasks")
@api.doc(
    summary="One project's tasks",
    description="The project's board: every task under it with checklist progress and output counts.",
)
def project_tasks(project_id: int):
    """One project's board without the rest of the database — what `kith project show` prints."""
    tasks = [t for t in brain.snapshot_tasks(AGENT_DB_PATH) if t.get("project_id") == project_id]
    return jsonify({"tasks": tasks})


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
@api.doc(
    summary="Kith's lifetime",
    description=(
        "Everything that has happened, merged newest-first. Bare: the newest 500 as an array. "
        "With ?page=1 or ?before=<iso> (and optionally ?limit=<n>, at most 2000): one page, "
        "strictly older than `before`, as {events, has_more, totals, message_kinds, oldest}."
    ),
)
def brain_timeline():
    """Two shapes, chosen by asking, so nothing that calls it bare has to change.

    The bare array is what the endpoint has always answered and is still right for "what
    happened lately". A page is for reading all the way back, which the array could not do:
    it was the newest 500 and nothing told you there were more. A bad `limit` is the default
    rather than a 400, as the conversation window's is; a `before` that is not a time is a 400,
    because guessing at a cursor is how a scroll repeats or skips a page.
    """
    args = request.args
    try:
        limit = int(args.get("limit", brain.PAGE))
    except ValueError:
        limit = brain.PAGE
    if limit < 1:
        limit = brain.PAGE
    if "before" not in args and args.get("page") != "1":
        return jsonify(brain.timeline(AGENT_DB_PATH, limit=min(limit, brain.MAX_PAGE)))
    before = args.get("before") or None
    if before is not None:
        try:
            when = datetime.fromisoformat(before)
        except ValueError:
            return jsonify({"error": f"before is not an ISO time: {before!r}"}), 400
        # Stored times are UTC with an offset (`utc_now_iso`), and they compare as text, so the
        # cursor is put in the same form: a `Z` or a +04:00 would otherwise sort wrongly.
        before = (when if when.tzinfo else when.replace(tzinfo=UTC)).astimezone(UTC).isoformat()
    return jsonify(brain.timeline_page(AGENT_DB_PATH, before=before, limit=limit))


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
