"""Who he is — the fragment files, editable.

The persona is a folder of markdown, and that stays true: these endpoints are file
operations, not a database behind a form. Someone who prefers a text editor can open the
folder and get the same result, which is the point of keeping it as files.
"""

from __future__ import annotations

from flask import jsonify, request

from kith.api.blueprint import api
from kith.services import persona


def _fail(exc: Exception, status: int = 400):
    return jsonify({"error": str(exc)}), status


@api.get("/persona")
@api.doc(
    summary="His persona, fragment by fragment",
    description=(
        "Every fragment including the disabled ones, in merge order, with its text. "
        "`merged` is what actually reaches the model — the fragments joined, comments "
        "stripped — so the size of the thing being edited is visible while editing it. "
        "`parts` is that same text still separated by the file it came from, because a "
        "reader who finds a sentence in the merge needs to know which fragment to open."
    ),
)
def get_persona():
    parts = persona.merged_parts()
    merged = "\n\n".join(part["text"] for part in parts)
    return jsonify(
        {
            "folder": str(persona.persona_dir()),
            "fragments": persona.fragments(),
            "parts": parts,
            "merged": merged,
            "chars": len(merged),
        }
    )


@api.post("/persona")
@api.doc(summary="Add a fragment", description="Refuses to overwrite an existing one.")
def create_fragment():
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(
            persona.create_fragment(str(payload.get("name") or ""), str(payload.get("body") or ""))
        )
    except persona.PersonaError as exc:
        return _fail(exc)


@api.put("/persona/<path:name>")
@api.doc(summary="Save a fragment")
def save_fragment(name: str):
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(persona.write_fragment(name, str(payload.get("body") or "")))
    except persona.PersonaError as exc:
        return _fail(exc)


@api.patch("/persona/<path:name>")
@api.doc(
    summary="Enable, disable, or rename a fragment",
    description=(
        "Both are renames underneath: disabling prefixes the filename with an underscore, "
        "which is the convention the loader reads, and the numeric prefix IS the merge "
        "order — so 'move this earlier' and 'rename this' are the same operation."
    ),
)
def patch_fragment(name: str):
    payload = request.get_json(silent=True) or {}
    try:
        if "enabled" in payload:
            return jsonify(persona.set_enabled(name, bool(payload["enabled"])))
        if payload.get("name"):
            return jsonify(persona.rename_fragment(name, str(payload["name"])))
    except persona.PersonaError as exc:
        return _fail(exc)
    return _fail(ValueError("nothing to change: send `enabled` or `name`"))


@api.delete("/persona/<path:name>")
@api.doc(summary="Remove a fragment", description="No undo — the interface asks first.")
def delete_fragment(name: str):
    try:
        persona.delete_fragment(name)
    except persona.PersonaError as exc:
        return _fail(exc, 404)
    return jsonify({"ok": True})
