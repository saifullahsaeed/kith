"""The agent tree under a conversation, for the interface to draw and the person to stop."""

from __future__ import annotations

from flask import jsonify

from kith.api.blueprint import api
from kith.kernel import live_turns
from kith.services import agents


@api.get("/conversations/<conversation_id>/agents")
@api.doc(
    summary="The agents under a conversation",
    description=(
        "Nested, closed ones included: name, state (working, waiting, done, stopped, closed), "
        "whether a turn is running or it is waiting on its handler's answer, its branch and "
        "worktree, and the last thing it said. `chain` is the path from the person's chat down "
        "to this conversation, for an agent's own page to show who it works for."
    ),
)
def agent_tree(conversation_id: str):
    me = agents.row(conversation_id) or {}
    return jsonify(
        {
            # The session itself, for the node the map draws its agents from.
            "self": {
                "id": conversation_id,
                "title": str(me.get("title") or ""),
                "name": agents.label(me) if me else "",
                "working": live_turns.current(conversation_id) is not None,
                **(agents.usage(me) if me else {}),
            },
            "agents": agents.tree(conversation_id),
            "root": agents.root_of(conversation_id),
            "chain": [
                {"id": str(one["id"]), "name": agents.label(one), "state": one.get("agent_state") or ""}
                for one in agents.chain(conversation_id)
            ],
        }
    )


@api.post("/conversations/<conversation_id>/agents/stop")
@api.doc(
    summary="Stop every agent under a conversation",
    description="Their turns stop and they stay resumable — the conversation that started them can message them again.",
)
def stop_agent_tree(conversation_id: str):
    return jsonify(agents.stop(conversation_id, ["all"]))
