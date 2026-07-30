"""Browsing the files he has written."""

from __future__ import annotations

from pathlib import Path

from flask import jsonify, request

from kith.api.blueprint import api
from kith.infra import default_app, sandbox
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
