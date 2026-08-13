"""MCP servers: list them, try one, save the list, reconnect.

The shape mirrors the connection routes, including the rule that matters: **trying is not
saving**. `POST /mcp/probe` starts a server, asks what it offers and stops it again without
writing anything, so testing a command that turns out to be wrong does not leave it
configured.
"""

from __future__ import annotations

from flask import jsonify, request

from kith.api.blueprint import api
from kith.domain.mcp import MCPServer
from kith.services import tuning
from kith.services.mcp import manager
from kith.settings import CONFIG_DB_PATH


def _timeouts() -> tuple[float, float]:
    return float(tuning.value("mcp_connect_timeout")), float(tuning.value("mcp_call_timeout"))


def _from_request(payload: dict) -> MCPServer:
    return MCPServer(
        label=str(payload.get("label") or "").strip().lower(),
        command=str(payload.get("command") or "").strip(),
        args=tuple(str(a) for a in (payload.get("args") or [])),
        env={str(k): str(v) for k, v in (payload.get("env") or {}).items()},
        enabled=bool(payload.get("enabled", True)),
    )


@api.get("/mcp")
@api.doc(
    summary="Configured MCP servers",
    description=(
        "Each server, whether it is switched on, and — for the ones currently connected — "
        "the tools it is contributing. Environment *names* are reported and their values "
        "never are: they are where an API token goes, and a settings page that round-trips "
        "one leaks it to anything that can read the response."
    ),
)
def list_mcp_servers():
    live = manager.running()
    return jsonify(
        {
            "servers": [
                {
                    **server.public(),
                    "connected": server.label in live,
                    "tools": [
                        {"name": t.get("name", ""), "description": t.get("description", "")}
                        for t in live.get(server.label, [])
                    ],
                }
                for server in manager.configured(CONFIG_DB_PATH)
            ]
        }
    )


@api.post("/mcp/probe")
@api.doc(
    summary="Try a server without saving it",
    description=(
        "Starts it in its own process, asks for its tools, and stops it. Writes nothing, and "
        "does not disturb the same server if it is already connected and mid-turn."
    ),
)
def probe_mcp_server():
    payload = request.get_json(silent=True) or {}
    connect_timeout, call_timeout = _timeouts()
    return jsonify(manager.probe(_from_request(payload), connect_timeout, call_timeout))


@api.put("/mcp")
@api.doc(
    summary="Save the server list",
    description=(
        "Replaces the whole list, which is how a settings page edits it. Labels must be "
        "unique: a label is a namespace, so two servers sharing one would contribute tools "
        "with identical names and the second would silently shadow the first."
    ),
)
def save_mcp_servers():
    payload = request.get_json(silent=True) or {}
    rows = payload.get("servers")
    if not isinstance(rows, list):
        return jsonify({"error": 'send {"servers": [...]}'}), 400
    try:
        saved = manager.save(CONFIG_DB_PATH, [_from_request(row) for row in rows])
    except ValueError as refused:
        return jsonify({"error": str(refused)}), 400
    # Bring up anything newly enabled straight away, so saving and it working are one action
    # rather than two. Failures are reported rather than raised: one broken server must not
    # stop the others being saved and started.
    connect_timeout, call_timeout = _timeouts()
    trouble = manager.connect(CONFIG_DB_PATH, connect_timeout, call_timeout)
    return jsonify({"servers": [s.public() for s in saved], "failed": trouble})


@api.post("/mcp/reconnect")
@api.doc(
    summary="Restart every server",
    description=(
        "Stops them all and brings the enabled ones back up. This is the answer to a server "
        "that has crashed or that you have just reinstalled — its tools are held for the "
        "rest of any turn in flight, deliberately, so nothing shorter than this reloads them."
    ),
)
def reconnect_mcp_servers():
    manager.disconnect_all()
    connect_timeout, call_timeout = _timeouts()
    trouble = manager.connect(CONFIG_DB_PATH, connect_timeout, call_timeout)
    live = manager.running()
    return jsonify(
        {
            "connected": sorted(live),
            "tools": {label: len(tools) for label, tools in live.items()},
            "failed": trouble,
        }
    )
