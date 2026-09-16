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
    {"plugin": {**STR, "description": "The plugin, named the way the summary line names it."}},
    required=("plugin",),
)
def plugin_state(path: Path, args: dict):
    from kith import settings as live
    from kith.services.plugins import registry, state

    asked = str(args.get("plugin") or "").strip()
    installed = registry.enabled(live.CONFIG_DB_PATH)
    found = _resolve(asked, installed)
    if found is None:
        known = ", ".join(f"{p.name} ({p.id})" for p in installed) or "none installed"
        return {"error": f"no plugin called {asked!r}. Installed: {known}"}
    try:
        return {"plugin": found, **state.read(path, found)}
    except state.PluginStateError as refused:
        return {"error": str(refused)}


def _resolve(asked: str, installed) -> str | None:
    """The plugin id behind whatever he typed.

    **The name in his context and the name this matched on were not the same string.** The digest
    line is headed by `state.digest`'s `lead`, which falls back to the plugin's display *name*;
    this asked for an exact match on its *id*. So the one label he is ever shown — "Sketchpad" —
    was the one label that did not resolve, and the tool's own description told him to use it.
    Guaranteed to cost a round every time, and a wrong-argument round is the kind he retries.

    Matched loosely rather than by fixing the description, because the fix that only changes
    prose is the fix that stops working the next time either end is edited. Id first, so an exact
    id can never be shadowed by somebody else's display name.
    """
    wanted = asked.casefold()
    for plugin in installed:
        if plugin.id.casefold() == wanted:
            return plugin.id
    if not wanted:
        return None
    for plugin in installed:
        declared = (plugin.state or {}).get("digest")
        lead = str(declared.get("lead") or "") if isinstance(declared, dict) else ""
        if wanted in {plugin.name.casefold(), lead.casefold()} - {""}:
            return plugin.id
    return None
