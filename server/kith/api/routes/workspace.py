"""Browsing the files he has written."""

from __future__ import annotations

from pathlib import Path

from flask import jsonify, request, send_file

from kith.api.blueprint import api
from kith.config import AGENT_DB_PATH
from kith.infra import default_app
from kith.infra import workspace as sandbox
from kith.infra.db import repositories as repo
from kith.services import handoff


def _anchor(path: str, project_id: str | None) -> str:
    """A relative path, anchored to a specific project's folder rather than guessed at.

    `resolve()` anchors a relative path to the *active session's* linked project — which
    a plain GET from the interface has none of, so it fell back to the global workspace
    root every time. That is invisible for anything he wrote directly into that root, and
    a flat "there's no <path>" for everything else: a task's deliverable, its plan doc,
    any relative path shown for a project with its own folder — all stored relative to the
    project that was live when he wrote them, none of it re-derivable from the path alone.

    The interface already knows which project a deliverable or a task's plan belongs to,
    so it is the one piece of context worth passing explicitly rather than assuming.
    Absolute paths and requests with no project id are returned unchanged — resolve() and
    the global root remain exactly as they were for everything else that reads through here.
    """
    if not project_id or Path(path).expanduser().is_absolute():
        return path
    try:
        project = repo.projects.get_project(AGENT_DB_PATH, int(project_id))
        directory = str((project or {}).get("directory") or "").strip()
        if directory:
            return str(Path(directory) / path)
    except Exception:
        pass
    return path


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
    path = _anchor(path, request.args.get("projectId"))
    try:
        # read_raw, not read_file: the viewer wants the real file, not the
        # line-numbered window the model reads.
        return jsonify({"path": path, "content": sandbox.read_raw(path)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@api.get("/workspace/raw")
@api.doc(
    summary="Serve a workspace file as itself",
    description=(
        "The bytes of one file at ?path=, with its own content type — so a picture or a "
        "PDF can be shown rather than decoded as text. Streamed, and answers range "
        "requests, which is how a PDF viewer reads a document."
    ),
)
def workspace_raw():
    path = request.args.get("path") or ""
    if not path:
        return jsonify({"error": "path required"}), 400
    path = _anchor(path, request.args.get("projectId"))
    try:
        target, kind = sandbox.media_file(path)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    # conditional=True gives ETag/If-Modified-Since and Range handling for free.
    # download_name is set but as_attachment is not, so a save keeps the real name
    # while a view still renders in place.
    return send_file(target, mimetype=kind, conditional=True, download_name=target.name)


@api.post("/workspace/open")
@api.doc(
    summary="Open or reveal a file in another application",
    description=(
        "Opens the file with the machine's default application, or shows it in the file "
        "manager when {reveal: true}. Takes {path} for one of his files, or {hostPath} "
        "for an absolute path — which must be inside a folder Kith owns. Anything "
        "executable is revealed rather than run. Body: {path | hostPath, reveal?}."
    ),
)
def workspace_open():
    body = request.get_json(silent=True) or {}
    path = str(body.get("path") or "").strip()
    host_path = str(body.get("hostPath") or "").strip()
    reveal = bool(body.get("reveal"))
    if not path and not host_path:
        return jsonify({"error": "path or hostPath required"}), 400
    try:
        # One of his files, by workspace path: the common case, and the whole action in
        # a single request.
        if path:
            path = _anchor(path, body.get("projectId"))
            return jsonify({"ok": True, **handoff.open_workspace_file(path, reveal=reveal).public()})
        # An absolute path — his databases, his persona folder, a transcript. Those are
        # not under his workspace, so they arrive already resolved.
        if reveal:
            handoff.reveal(Path(host_path))
        else:
            handoff.open_with_default_app(Path(host_path))
    except handoff.HandoffError as refused:
        return jsonify({"error": str(refused)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
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


@api.post("/workspace/root")
@api.doc(
    summary="Change the folder he works in",
    description=(
        "Points him at a different folder. Nothing is moved — his existing work stays "
        "where it is. Refuses a folder broad enough to defeat the permission boundary, "
        "since he works unprompted inside whichever folder this is. Body: {path}."
    ),
)
def workspace_set_root():
    body = request.get_json(silent=True) or {}
    try:
        chosen = sandbox.set_root(str(body.get("path") or ""))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"path": str(chosen)})


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
