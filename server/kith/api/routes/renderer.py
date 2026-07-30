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
