"""Which MCP servers are configured, which are running, and what they offer.

Mirrors `services/connections/` deliberately, including the rule that matters most there:
**trying is not saving.** `probe` starts a server, asks what it has, and writes nothing.
Only `save` persists. Without that split, testing a command that turns out to be wrong
leaves it configured.

Two things here are not obvious and are the reason this is a service rather than a few
functions.

**The tool list is cached, and the cache is the point.** `tool_schemas()` is rebuilt on
every round of every turn and its bytes are part of the cached prompt prefix. A live
`tools/list` there would put a subprocess round-trip in the hot path — and worse, a server
dying mid-turn would silently shrink the tool list, which changes the prefix and throws away
the whole cached prompt on the next round. So the list is fetched at connect and held.

**And a turn takes a snapshot.** Even the cached list can change under a turn — a refresh, a
server switched off in another tab. `snapshot()` freezes it for the duration, so the tools
block is byte-identical from the first round to the last. A server that dies mid-turn keeps
its schemas; its calls then fail with something readable instead of costing the cache.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from kith.domain.mcp import MCPServer, split_tool_name, tool_name
from kith.infra.db import config_store
from kith.services.mcp.client import MCPError, StdioServer

#: Where the server list lives.
#:
#: In the config database rather than the tuning registry, which holds bounded scalars with
#: a default and a min and a max. A list of servers has none of those, and forcing it in
#: would mean either a knob per field or JSON in a text box.
SERVERS_KEY = "mcp.servers"


class _Live:
    """A connected server and what it offered when we asked."""

    def __init__(self, server: MCPServer, process: StdioServer, tools: list[dict]):
        self.server = server
        self.process = process
        self.tools = tools


_live: dict[str, _Live] = {}
_lock = threading.Lock()


# -- what is configured ----------------------------------------------------- #


def configured(config_db: Path) -> list[MCPServer]:
    raw = config_store.load_settings(config_db).get(SERVERS_KEY)
    try:
        rows = json.loads(raw) if isinstance(raw, str) else list(raw or [])
    except (TypeError, ValueError):
        return []
    return [MCPServer.from_stored(row) for row in rows if isinstance(row, dict)]


def save(config_db: Path, servers: list[MCPServer]) -> list[MCPServer]:
    """Persist the list, refusing anything structurally broken or duplicated.

    Labels are unique because a label is a namespace: two servers called `files` would
    contribute tools with identical names, and the second would silently shadow the first
    with no way to tell which one a call reached.
    """
    seen: set[str] = set()
    for server in servers:
        problems = server.problems()
        if problems:
            raise ValueError(f"{server.label or '(no label)'}: {problems[0]}")
        if server.label in seen:
            raise ValueError(f"there is already a server called {server.label!r}")
        seen.add(server.label)
    config_store.update_settings(config_db, {SERVERS_KEY: json.dumps([s.stored() for s in servers])})
    # Anything removed or renamed should stop running now rather than at the next restart.
    _retire(seen)
    return servers


def _retire(keep: set[str]) -> None:
    with _lock:
        for label in [name for name in _live if name not in keep]:
            _live.pop(label).process.stop()


def forget(config_db: Path, label: str) -> list[MCPServer]:
    remaining = [s for s in configured(config_db) if s.label != label]
    return save(config_db, remaining)


# -- trying, which never saves ---------------------------------------------- #


def probe(server: MCPServer, connect_timeout: float, call_timeout: float) -> dict:
    """Start it, ask what it has, stop it. Writes nothing.

    Its own process every time, separate from any running one, so probing a server that is
    already connected cannot disturb the conversation a turn is having with it.
    """
    problems = server.problems()
    if problems:
        return {"ok": False, "detail": problems[0], "tools": []}
    process = StdioServer(server.command, list(server.args), server.env, connect_timeout)
    try:
        process.start()
        tools = process.list_tools(call_timeout)
        return {
            "ok": True,
            "detail": _describe(process.server_info),
            "tools": [_public_tool(t) for t in tools],
        }
    except MCPError as failure:
        return {"ok": False, "detail": str(failure), "tools": []}
    finally:
        process.stop()


def _describe(info: dict) -> str:
    name = str(info.get("name") or "").strip()
    version = str(info.get("version") or "").strip()
    return f"{name} {version}".strip() or "connected"


def _public_tool(tool: dict) -> dict:
    return {"name": str(tool.get("name") or ""), "description": str(tool.get("description") or "")}


# -- running ---------------------------------------------------------------- #


def connect(config_db: Path, connect_timeout: float, call_timeout: float) -> dict[str, str]:
    """Bring every enabled server up. Returns label -> what went wrong, for the ones that did.

    Idempotent, and safe to call on a server already running: an existing live entry whose
    process is still alive is left exactly as it is, tool list included, because replacing it
    would change the tools block mid-turn.
    """
    trouble: dict[str, str] = {}
    for server in configured(config_db):
        if not server.enabled:
            continue
        with _lock:
            existing = _live.get(server.label)
            if existing is not None and existing.process.alive:
                continue
        process = StdioServer(server.command, list(server.args), server.env, connect_timeout)
        try:
            process.start()
            tools = process.list_tools(call_timeout)
        except MCPError as failure:
            process.stop()
            trouble[server.label] = str(failure)
            continue
        with _lock:
            stale = _live.pop(server.label, None)
            _live[server.label] = _Live(server, process, tools)
        if stale is not None:
            stale.process.stop()
    return trouble


def disconnect_all() -> None:
    with _lock:
        live = list(_live.values())
        _live.clear()
    # Stopped outside the lock: terminate() waits, and holding the lock through it would
    # block every tool call in flight behind a shutdown.
    for entry in live:
        entry.process.stop()


def running() -> dict[str, list[dict]]:
    """label -> its tools, as last fetched."""
    with _lock:
        return {label: list(entry.tools) for label, entry in _live.items()}


# -- what the model sees ---------------------------------------------------- #


def snapshot() -> list[dict]:
    """Every enabled server's tools, as function schemas, frozen for one turn.

    Taken once at the top of a turn and passed down, so the tools block is byte-identical
    from the first round to the last. Rebuilding it per round would mean a server dying —
    or being switched off in another tab — silently shrinking the block, which changes the
    cached prefix and discards the whole prompt cache on the next round.
    """
    out: list[dict] = []
    with _lock:
        entries = list(_live.items())
    for label, entry in entries:
        for tool in entry.tools:
            name = str(tool.get("name") or "").strip()
            if not name:
                continue
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool_name(label, name),
                        "description": _description(label, tool),
                        "parameters": _parameters(tool),
                    },
                }
            )
    # Sorted so the block is stable across restarts and dict ordering. An unstable order is
    # a different byte sequence, which is a cache miss on the whole prefix for no reason.
    out.sort(key=lambda schema: schema["function"]["name"])
    return out


def _description(label: str, tool: dict) -> str:
    text = str(tool.get("description") or "").strip()
    return f"[{label}] {text}" if text else f"A tool from the {label} server."


def _parameters(tool: dict) -> dict:
    """The tool's input schema, in the shape the providers expect.

    MCP calls it `inputSchema` and it is already JSON Schema, so this is a rename rather than
    a translation. A server that sends something unusable gets an empty object instead: a
    malformed schema is a 400 on the *whole* request, so one bad tool would take down every
    turn rather than just itself.
    """
    schema = tool.get("inputSchema")
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return {"type": "object", "properties": {}, "required": []}
    return {
        "type": "object",
        "properties": schema.get("properties") if isinstance(schema.get("properties"), dict) else {},
        "required": [str(r) for r in (schema.get("required") or []) if isinstance(r, str)],
    }


def owns(name: str) -> bool:
    """Is this name MCP's to answer? Decided by the namespace, not by what is running.

    It used to check the live registry, and that was wrong in the one case the design exists
    for. A turn holds its tool snapshot even after a server dies, so the model *will* call a
    tool whose server has gone — and asking `_live` made `run_tool` fall through to "unknown
    tool: mcp__probe__add", which tells him he invented a tool he was handed two rounds ago.
    He would then stop trying anything from that server rather than route around one failure.

    Claiming every well-formed `mcp__…__…` name is safe precisely because of the namespace:
    nothing else in the registry can be spelled that way, so there is nothing to steal. What
    it buys is an accurate message from `run` below.
    """
    label, tool = split_tool_name(name)
    return bool(label and tool)


def run(name: str, arguments: dict, call_timeout: float) -> dict:
    """Call one, in the {ok, result} envelope the rest of the loop speaks."""
    label, tool = split_tool_name(name)
    with _lock:
        entry = _live.get(label)
    if entry is None:
        # He was handed this tool's schema at the top of the turn and the server has since
        # gone, so the useful half of this sentence is the second one: one dead server is
        # not a reason to abandon the task.
        return {
            "ok": False,
            "error": f"the {label!r} server is not connected. Carry on without it.",
        }
    try:
        answer = entry.process.call(tool, arguments or {}, call_timeout)
    except MCPError as failure:
        # The schemas stay in the block for the rest of the turn even though the server is
        # gone — see `snapshot`. This is what he reads instead, and it says what to do.
        return {"ok": False, "error": f"{label}: {failure}. Carry on without it."}
    if answer.get("isError"):
        # The tool ran and refused. That is a result he can act on, not a transport failure.
        return {"ok": False, "error": str(answer.get("content") or "the tool reported an error")}
    return {"ok": True, "result": answer.get("content", "")}


def connect_async(config_db: Path) -> None:
    """Bring the enabled servers up in the background.

    Off the startup path deliberately. A server installed by `npx` or `uvx` downloads its
    package the first time it runs, which can take tens of seconds, and that must not be
    what stands between launching the app and it answering. A turn that begins before they
    are up simply sees no MCP tools — which is the honest state, and the next turn has them.
    """

    def _run() -> None:
        from kith.services import tuning

        try:
            trouble = connect(
                config_db,
                float(tuning.value("mcp_connect_timeout")),
                float(tuning.value("mcp_call_timeout")),
            )
        except Exception as exc:
            print(f"[kith] mcp: could not connect ({exc})")
            return
        live = running()
        for label, tools in sorted(live.items()):
            print(f"[kith] mcp: {label} connected, {len(tools)} tool(s)")
        for label, why in sorted(trouble.items()):
            print(f"[kith] mcp: {label} failed — {why}")

    threading.Thread(target=_run, name="kith-mcp-connect", daemon=True).start()
