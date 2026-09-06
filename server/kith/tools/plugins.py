"""Reading what a plugin is holding.

One tool with the plugin as an argument, not one tool per plugin: N plugins would put N schemas
in a block carried on every round of every turn, for something read rarely. The same
shape-beats-instruction reasoning that made `read_skill` one tool.

**There is deliberately no write.** Every write the model could want is a plugin action it can
already take — a declared command, or a call to the plugin's own MCP server. Leaving the write
out deletes an argument, a merge policy, and the question of what happens when a live surface
and the model disagree about one key.

**And no per-turn dedupe**, unlike `read_skill`. A skill's instructions cannot change mid-turn;
plugin state can, because the person is clicking the surface while he works. Deduping would
return a stale answer to the one question whose entire point is freshness.
"""

from __future__ import annotations

from pathlib import Path

from kith.tools.params import STR
from kith.tools.registry import tool


@tool(
    "plugin_state",
    "What one of your plugins is holding right now. The line about it in your context is a "
    "summary taken when the turn began; this is current, and it is the whole of what the "
    "plugin has stored rather than the few keys the summary shows. Read it when the summary "
    "is not enough to act on — not before.",
    {"plugin": {**STR, "description": "The plugin's name, exactly as the summary lists it."}},
    required=("plugin",),
)
def plugin_state(path: Path, args: dict):
    from kith import settings as live
    from kith.services.plugins import registry, state

    name = str(args.get("plugin") or "").strip()
    installed = registry.enabled(live.CONFIG_DB_PATH)
    if not any(plugin.id == name for plugin in installed):
        known = ", ".join(plugin.id for plugin in installed) or "none installed"
        return {"error": f"no plugin called {name!r}. Installed: {known}"}
    try:
        return {"plugin": name, **state.read(path, name)}
    except state.PluginStateError as refused:
        return {"error": str(refused)}
