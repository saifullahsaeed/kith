"""What he may do on your machine — the mode, and answering his requests."""

from __future__ import annotations

from flask import jsonify, request

from kith.api.blueprint import api
from kith.infra import permissions, workspace


@api.get("/permissions")
@api.doc(
    summary="Current stance",
    description="The mode, anything he is waiting on, and what has been granted.",
)
def get_permissions():
    return jsonify({**permissions.snapshot(), "workspace": workspace.status()})


@api.post("/permissions/mode")
@api.doc(summary="Set the mode", description="One of ask, auto, bypass.")
def set_permission_mode():
    payload = request.get_json(silent=True) or {}
    try:
        permissions.set_mode(str(payload.get("mode") or ""))
    except ValueError:
        return jsonify({"error": "mode must be ask, auto or bypass"}), 400
    return jsonify(permissions.snapshot())


@api.post("/permissions/<request_id>/approve")
@api.doc(
    summary="Allow something he asked for",
    description="`scope` is session (default) or always. Session grants end with the process.",
)
def approve_permission(request_id: str):
    payload = request.get_json(silent=True) or {}
    scope = str(payload.get("scope") or "session")
    try:
        granted = permissions.approve(request_id, scope)
    except KeyError:
        return jsonify({"error": "that request is no longer waiting"}), 404
    return jsonify({"approved": granted, **permissions.snapshot()})


@api.post("/permissions/<request_id>/deny")
@api.doc(summary="Refuse something he asked for")
def deny_permission(request_id: str):
    try:
        permissions.deny(request_id)
    except KeyError:
        return jsonify({"error": "that request is no longer waiting"}), 404
    return jsonify(permissions.snapshot())


@api.post("/permissions/revoke")
@api.doc(
    summary="Forget standing grants",
    description=(
        "With no body, forgets every standing grant. With {signature}, forgets just that "
        "one — keeping the ones you still mean, which is the only way anyone actually "
        "wants to manage these."
    ),
)
def revoke_permissions():
    payload = request.get_json(silent=True) or {}
    signature = str(payload.get("signature") or "").strip()
    if signature:
        permissions.revoke(signature)
    else:
        permissions.revoke_all()
    return jsonify(permissions.snapshot())
