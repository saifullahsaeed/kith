"""The desktop app offering its Chromium for rendering pages."""

from __future__ import annotations

from flask import jsonify, request

from kith.api.blueprint import api
from kith.infra import renderer


@api.get("/renderer")
@api.doc(
    summary="Renderer status",
    description="Whether a desktop Chromium is registered for rendering pages, and where.",
)
def renderer_status():
    return jsonify({"registered": renderer.available(), "renderer": renderer.describe()})


@api.post("/renderer")
@api.doc(
    summary="Register the desktop renderer",
    description=(
        "The desktop app offers its own Chromium for rendering JS-heavy pages, so "
        "Kith doesn't need a second browser in his sandbox. The shell calls this at "
        "startup with a loopback URL and a per-launch token; DELETE clears it."
    ),
)
def register_renderer():
    body = request.get_json(silent=True) or {}
    try:
        renderer.register(body.get("url", ""), body.get("token", ""))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"registered": True, "renderer": renderer.describe()})


@api.delete("/renderer")
@api.doc(summary="Forget the desktop renderer")
def forget_renderer():
    renderer.unregister()
    return jsonify({"registered": False})


@api.post("/system/notify")
@api.doc(
    summary="Post a native notification",
    description=(
        "Goes out through the desktop shell, which is the only process with an app "
        "identity macOS will attribute a notification to. Answers `{shown: false}` when "
        "Kith is running as a bare server rather than in the app — that is a fact about "
        "the setup, not an error."
    ),
)
def system_notify():
    payload = request.get_json(silent=True) or {}
    shown = renderer.notify(
        str(payload.get("title") or "Kith"),
        str(payload.get("body") or ""),
    )
    return jsonify({"shown": shown})


@api.post("/system/pick-folder")
@api.doc(
    summary="Ask for a folder with the system dialog",
    description=(
        "Shows the platform's own folder chooser and returns what was picked. "
        '{"path": ""} means they cancelled; {"available": false} means there is no '
        "desktop shell to ask, and the interface should take a typed path instead."
    ),
)
def system_pick_folder():
    payload = request.get_json(silent=True) or {}
    chosen = renderer.pick_folder(
        title=str(payload.get("title") or ""),
        start=str(payload.get("start") or ""),
    )
    if chosen is None:
        return jsonify({"available": False, "path": ""})
    return jsonify({"available": True, "path": chosen})


@api.post("/system/settings-pane")
@api.doc(
    summary="Open a macOS settings pane",
    description="One of: fullDisk, notifications, files. Named rather than given as a URL.",
)
def system_settings_pane():
    payload = request.get_json(silent=True) or {}
    opened = renderer.open_settings_pane(str(payload.get("pane") or ""))
    return jsonify({"opened": opened})
