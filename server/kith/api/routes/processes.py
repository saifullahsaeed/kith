"""Background tasks, for the panel.

`check_process` is his view of these — what one has printed since he last looked. This is yours: a
list of what is running, so a half-hour test suite is visible while it runs rather than being
something you find out about when he mentions it.

Read-only on purpose. Starting and stopping are his, through the tools, because a process he started
and you killed is a turn that continues against a world that changed under it.
"""

from __future__ import annotations

from flask import jsonify

from kith.api.blueprint import api
from kith.services.code import processes


@api.get("/processes")
@api.doc(summary="Background tasks", description="What is running in the background, and for how long.")
def list_processes():
    try:
        return jsonify(processes.processes.check())
    except processes.ProcessError:
        # Nothing running is not an error, and the panel must not blank out over one.
        return jsonify({"running": []})
