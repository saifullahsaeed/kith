"""Is there a newer Kith than this one.

Read by two surfaces — the settings screen and the menu bar — which is why it is an endpoint
rather than something the shell works out for itself. One check, one cache, one answer.
"""

from __future__ import annotations

from flask import jsonify

from kith.api.blueprint import api
from kith.services import updates


@api.get("/update")
@api.doc(
    summary="Whether there is a newer release",
    description=(
        "Cached for six hours. `packaged` is false when Kith is running from a checkout, where "
        "there is no installed version to compare against — which is not the same as being up "
        "to date, and the interface says so differently. `error` is kept rather than swallowed "
        "so a screen can tell 'could not reach GitHub' from 'you are current'."
    ),
)
def update_state():
    return jsonify(updates.check())


@api.post("/update/check")
@api.doc(summary="Look now", description="Ignores the cache. What the 'Check now' button calls.")
def update_check():
    return jsonify(updates.check(force=True))
