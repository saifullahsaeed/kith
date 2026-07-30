"""Browsing the files he has written."""

from __future__ import annotations

from pathlib import Path

from flask import jsonify, request

from kith.api.blueprint import api
from kith.infra import default_app
from kith.infra import workspace as sandbox
from kith.services import handoff


@api.get("/workspace")
@api.doc(
    summary="Browse his workspace",
    description="One-level listing of Kith's sandbox files at ?path= (default his home).",
)
def workspace_list():
    path = request.args.get("path") or "."
    try:
        return jsonify({"path": path, "entries": sandbox.list_dir(path)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@api.get("/workspace/file")
@api.doc(
    summary="Read a workspace file",
    description="The text content of one file in his sandbox (?path=), exactly as it is on disk.",
)
def workspace_file():
    path = request.args.get("path") or ""
    if not path:
        return jsonify({"error": "path required"}), 400
    try:
        # read_raw, not read_file: the viewer wants the real file, not the
        # line-numbered window the model reads.
        return jsonify({"path": path, "content": sandbox.read_raw(path)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@api.post("/workspace/handoff")
@api.doc(
    summary="Copy a file out of the sandbox onto this machine",
    description=(
        "Copies one of his files to the handoff folder and reports where it landed, "
        "so it can be opened in whatever you normally use for that file type. "
        "Body: {path}. Overwrites an earlier copy, so you get the current version."
    ),
)
def workspace_handoff():
    body = request.get_json(silent=True) or {}
    path = str(body.get("path") or "").strip()
    if not path:
        return jsonify({"error": "path required"}), 400
    try:
        return jsonify(handoff.export(path).public())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@api.post("/workspace/open")
@api.doc(
    summary="Open or reveal a handed-off file",
    description=(
        "Opens the file with the machine's default application, or shows it in the "
        "file manager when {reveal: true}. Only paths inside the handoff folder are "
        "accepted, and anything executable is revealed rather than run. "
        "Body: {hostPath, reveal?}."
    ),
)
def workspace_open():
    body = request.get_json(silent=True) or {}
    host_path = str(body.get("hostPath") or "").strip()
    if not host_path:
        return jsonify({"error": "hostPath required"}), 400
    try:
        if body.get("reveal"):
            handoff.reveal(Path(host_path))
        else:
            handoff.open_with_default_app(Path(host_path))
    except handoff.HandoffError as refused:
        return jsonify({"error": str(refused)}), 400
    return jsonify({"ok": True})


@api.get("/workspace/opens-with")
@api.doc(
    summary="Which application would open this file",
    description=(
        "The name of the machine's handler for a file's type — 'Microsoft Excel', "
        "'Preview' — so a button can say what it will do. Resolved from the extension, "
        "so nothing is copied out of the sandbox to answer it. Null when unknown, or "
        "on a platform where this can't be determined."
    ),
)
def workspace_opens_with():
    name = request.args.get("path") or ""
    if not name:
        return jsonify({"error": "path required"}), 400
    return jsonify({"opensWith": default_app.for_filename(name)})


@api.post("/workspace/folder")
@api.doc(
    summary="Create a folder",
    description="Makes a folder in his workspace, with any parent it needs. Body: {path}.",
)
def workspace_make_folder():
    return _mutate(lambda body: sandbox.make_dir(_relative(body.get("path"))))


@api.post("/workspace/rename")
@api.doc(
    summary="Rename or move",
    description=(
        "Renames a file or folder, or moves it elsewhere in his workspace. Refuses when "
        "something already has that name rather than replacing it. Body: {path, to}."
    ),
)
def workspace_rename():
    return _mutate(lambda body: sandbox.move(_relative(body.get("path")), _relative(body.get("to"))))


@api.delete("/workspace/file")
@api.doc(
    summary="Delete a file or folder",
    description=(
        "Deletes it, and everything inside if it's a folder. His home itself is refused. Body: {path}."
    ),
)
def workspace_delete():
    return _mutate(lambda body: sandbox.remove(_relative(body.get("path"))))


def _relative(raw: object) -> str:
    """A workspace-relative path, or a ValueError.

    Absolute paths and ``..`` are refused rather than repaired. An earlier version
    stripped slashes first and *then* looked for a leading one, so ``/etc/passwd``
    quietly became ``etc/passwd`` — harmless only because ``resolve()`` anchors
    relative paths at his home, which makes the guarantee an accident of another
    function rather than something this one enforces.
    """
    path = str(raw or "").strip()
    if not path:
        raise ValueError("path required")
    if path.startswith("/") or path.startswith("~"):
        raise ValueError("give a path inside his workspace, not an absolute one")
    parts = [part for part in Path(path).parts if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ValueError("that path is outside his workspace")
    if not parts:
        # "." or "./" — his home. Deleting or renaming that is never the intent.
        raise ValueError("that's his workspace itself — pick something inside it")
    return "/".join(parts)


def _mutate(action):
    """Run one workspace change, turning any refusal into a 400 with its reason."""
    body = request.get_json(silent=True) or {}
    try:
        action(body)
    except ValueError as bad:
        return jsonify({"error": str(bad)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})
