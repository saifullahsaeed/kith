"""Background tasks, for the panel.

`check_process` is his view of these — what one has printed since he last looked. This is yours: a
list of what is running, so a half-hour test suite is visible while it runs rather than being
something you find out about when he mentions it.

Read-only on purpose. Starting and stopping are his, through the tools, because a process he started
and you killed is a turn that continues against a world that changed under it.
"""

from __future__ import annotations

from flask import jsonify, request

from kith.api.blueprint import api
from kith.services.code import processes


@api.get("/processes")
@api.doc(
    summary="Background tasks",
    description="What this conversation is running in the background, and for how long.",
)
def list_processes():
    # Scoped to one conversation, always. Two projects are two chats, and a panel that listed every
    # session's work would show you a test suite you cannot explain, stop, or take credit for.
    # Absent means no conversation — the desktop shell asking in general — and lists everything.
    conversation_id = str(request.args.get("conversation") or "")
    try:
        return jsonify(processes.processes.check(conversation_id=conversation_id))
    except processes.ProcessError:
        # Nothing running is not an error, and the panel must not blank out over one.
        return jsonify({"running": []})
