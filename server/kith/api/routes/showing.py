"""What he last asked you to look at.

`kernel/changes` carries no values, by design — it says a kind of thing moved and the client
refetches. This is the thing to refetch. See `services/showing` for why it lives in memory.
"""

from __future__ import annotations

from flask import jsonify

from kith.api.blueprint import api
from kith.services import showing


@api.get("/showing")
@api.doc(
    summary="What he is putting in front of you",
    description=(
        "The last thing he asked you to look at — a design board, so far — or null. The "
        "interface opens it. Answers null once it has been taken."
    ),
)
def what_he_is_showing():
    return jsonify({"showing": showing.current()})


@api.delete("/showing")
@api.doc(
    summary="Take it",
    description="Clears it, so reopening the app does not reopen a board from last Tuesday.",
)
def take_it():
    showing.clear()
    return jsonify({"ok": True})
