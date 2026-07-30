"""Reading and changing how Kith behaves.

Kept apart from ``/config``, which holds the chat parameters a conversation depends on
(model, persona, context). These are the operational knobs: how long a turn may run,
how often he acts on his own, how quickly a loop is caught. Different audience,
different blast radius, and worth being able to reset independently.

The response carries each knob's own label, help, bounds and default, so the settings
UI is generated rather than keeping a second copy of all of that in TypeScript.
"""

from __future__ import annotations

from flask import jsonify, request

from kith.api.blueprint import api
from kith.services import tuning


@api.get("/tuning")
@api.doc(
    summary="Every configurable setting and its effective value",
    description=(
        "Grouped for display, each entry carrying its label, explanation, bounds, "
        "default, current value, and whether an environment variable is overriding it. "
        "Also returns the deployment paths, which are read-only."
    ),
)
def tuning_state():
    return jsonify(tuning.snapshot())


@api.patch("/tuning")
@api.doc(
    summary="Change settings",
    description=(
        "Body is {key: value} for one or more settings. Values are clamped to each "
        "setting's bounds rather than rejected, and an unknown key fails the whole "
        "batch so a typo cannot half-apply. Takes effect on his next turn — no restart."
    ),
)
def tuning_update():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict) or not body:
        return jsonify({"error": "send at least one setting to change"}), 400
    try:
        applied = tuning.apply(body)
    except ValueError as refused:
        return jsonify({"error": str(refused)}), 400
    return jsonify({"applied": applied, "state": tuning.snapshot()})


@api.post("/tuning/reset")
@api.doc(
    summary="Restore defaults",
    description=(
        "Body {keys: [...]} resets those settings; an empty body resets all of them. "
        "The stored value is removed rather than overwritten with today's default, so "
        "a future change to a default reaches anyone who never customised it."
    ),
)
def tuning_reset():
    body = request.get_json(silent=True) or {}
    keys = body.get("keys") or None
    if keys is not None and not isinstance(keys, list):
        return jsonify({"error": "keys must be a list"}), 400
    try:
        tuning.reset(keys)
    except ValueError as refused:
        return jsonify({"error": str(refused)}), 400
    return jsonify(tuning.snapshot())
