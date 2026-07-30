"""His mind: the whole snapshot, his lifetime, and generic edits by kind."""

from __future__ import annotations

from flask import jsonify, request

from kith.api.blueprint import api
from kith.config import AGENT_DB_PATH
from kith.infra.db import repositories as repo
from kith.services import brain


@api.get("/brain")
@api.doc(
    summary="Kith's whole database",
    description="Snapshot of memories, notes, journal, tasks, and self-made tools.",
)
def brain_snapshot():
    return jsonify(brain.snapshot(AGENT_DB_PATH))


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


@api.get("/mood")
@api.doc(
    summary="Kith's mood", description="His current felt state — label, energy (0–100), and an optional note."
)
def mood():
    return jsonify(repo.self_model.get_mood(AGENT_DB_PATH))
