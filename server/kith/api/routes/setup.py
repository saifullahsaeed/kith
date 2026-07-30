"""First-run setup: choosing where Kith thinks, and seeing what else is ready.

Kept apart from ``/config`` on purpose. That endpoint edits a *working* setup, one
field at a time, and persists whatever it is given. This one has to let someone try a
connection that might be wrong — repeatedly, while typing — and must therefore be able
to validate WITHOUT saving. Mixing the two would mean a rejected key becoming the
stored configuration.

Thin by design: every decision lives in ``services.connections``. These handlers only
read a body, hand it to the manager, and turn the answer into JSON.
"""

from __future__ import annotations

from flask import jsonify, request

from kith.api.blueprint import api
from kith.domain.connection import ProviderKind
from kith.services import connections

#: Named once so the error message and the check cannot drift apart.
_KINDS = ", ".join(str(kind) for kind in ProviderKind)


@api.get("/setup")
@api.doc(
    summary="Setup state",
    description=(
        "Whether Kith is ready to think, the provider choices, and what else is "
        "available — his sandbox, semantic recall, and search. Everything except a "
        "place to think is a warning rather than a blocker."
    ),
)
def setup_state():
    return jsonify(connections.snapshot())


@api.post("/setup/probe")
@api.doc(
    summary="Try a connection without saving it",
    description=(
        "Validates a candidate provider and returns the models it offers. Nothing is "
        "persisted, so onboarding can check a key as it is typed and a wrong one never "
        "becomes the saved configuration. Body: {kind, baseUrl?, apiKey?}."
    ),
)
def setup_probe():
    body = request.get_json(silent=True) or {}
    try:
        candidate = connections.manager.candidate(
            kind=str(body.get("kind") or ""),
            base_url=str(body.get("baseUrl") or ""),
            api_key=str(body.get("apiKey") or ""),
        )
    except ValueError:
        return jsonify({"error": f"kind must be one of: {_KINDS}"}), 400

    return jsonify(connections.manager.probe(candidate).public())


@api.post("/setup/complete")
@api.doc(
    summary="Save a chosen connection",
    description=(
        "Persists the connection and marks onboarding done. Re-probes first: a stale "
        "browser tab must not be able to save a connection that has since stopped "
        "working. Body: {kind, baseUrl?, apiKey?, model}."
    ),
)
def setup_complete():
    body = request.get_json(silent=True) or {}
    try:
        candidate = connections.manager.candidate(
            kind=str(body.get("kind") or ""),
            base_url=str(body.get("baseUrl") or ""),
            api_key=str(body.get("apiKey") or ""),
            model=str(body.get("model") or ""),
        )
    except ValueError:
        return jsonify({"error": f"kind must be one of: {_KINDS}"}), 400

    try:
        saved, warnings = connections.manager.adopt(candidate)
    except ValueError as refused:
        # Everything adopt() rejects is something the user can fix — a missing model,
        # a bad key, an unreachable host — so it is a 400 with the reason, not a 500.
        return jsonify({"error": str(refused)}), 400

    return jsonify({"connection": saved.public(), "warnings": warnings})
