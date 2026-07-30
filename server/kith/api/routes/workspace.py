"""Browsing the files he has written."""

from __future__ import annotations

from flask import jsonify, request

from kith.api.blueprint import api
from kith.infra import sandbox


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
