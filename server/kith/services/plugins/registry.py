"""What is installed, what the person decided about it, and the merged views everything reads.

Three stores, one rule each, and the rule is what keeps a plugin from quietly re-configuring
the app:

===========================================  ================================  =================
store                                        owns                              written by
===========================================  ================================  =================
``plugins/<id>/kith.plugin.json``            what the plugin **offers**        the publisher
config DB, ``plugins.installed``             what the person **decided**       Kith, on a human act
config DB, ``permission_grants``             whether its program **may run**   the install review
===========================================  ================================  =================

A manifest value is a default; a row value is a decision; **a decision always beats a
default**. There is deliberately no ``enabled`` field in a manifest, no environment *values*,
and no digest on-switch — so the failure `mcp.manager._retire`'s docstring records (a server
switched off that went on running and went on costing tokens) is not handled here, it is
unrepresentable.

**The row is the existence test.** `installed()` reads the row map first and resolves the
folder second, so a folder dropped in by hand contributes nothing to anything. That is what
makes install a transaction rather than a copy: the folder can be half-there for a syscall and
still be invisible.

**One unreadable plugin never costs the others.** Same rule `skills.installed()` follows: a
broken one is skipped and reported, because taking the rest of the list down with it would make
the failure look like the feature not existing.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from kith import settings
from kith.domain.mcp import MCPServer
from kith.domain.plugins import Plugin, PluginError, parse
from kith.infra.db import config_store

#: Where the person's decisions live. The config database rather than `settings.json`, for the
#: reason `permissions._store()` states as a standing order: Kith can edit `settings.json` with
#: his own file tools, and a safety control the guarded party can rewrite is not a control. An
#: install decision is safety-relevant. Not the plugin's own folder either, obviously — that
#: would let a plugin keep the record of its own permissions.
ROWS_KEY = "plugins.installed"

#: Where a partial install lives, and nowhere else. Dot-prefixed so `installed()` skips it even
#: if the row map ever disagreed, and under the plugins root so the commit is one `os.rename` on
#: one filesystem rather than a copy that can fail halfway.
STAGING = ".staging"

#: Where a plugin's own files live: `<plugins>/.storage/<id>`, a sibling of its code.
#:
#: **Not inside the plugin's folder, which is the whole point.** It was `<plugins>/<id>/.home`,
#: and install replaces that folder with one `os.rename` over an `rmtree` — so every upgrade
#: destroyed everything the plugin had written, while the store kept the *paths* to it and a
#: surface came back from an upgrade showing a broken image. Uninstall had the same hole against
#: its own stated rule: its comment says storage "is the person's data" and follows the
#: thirty-day retirement, and it trashed it with the code.
#:
#: Dot-prefixed for the same reason `.staging` is: `installed()` walks rows rather than
#: directories, so a sibling folder is never mistaken for a plugin.
STORAGE = ".storage"

#: How long a plugin's state survives being uninstalled.
#:
#: Uninstall marks rather than deletes. `skills.remove()` trashes rather than destroys, with the
#: explicit reasoning that it is "a folder of their writing, not a cache", and a plugin's stored
#: state is the same bytes: the person's data, which `disable` already keeps. Destroying it on
#: uninstall and keeping it on disable would be the same bytes treated two ways for no reason
#: anybody could state.
RETIRED_DAYS = 30

#: The enabled set is read on every chat request through the prompt build, so it is cached the
#: shape `permissions` already caches `permission_mode`: a short TTL, invalidated on every write.
_CACHE_SECONDS = 5.0
_cache: tuple[float, frozenset[str]] | None = None
_lock = threading.Lock()


def root() -> Path:
    """The plugins folder, created if it is not there. Nothing is bundled and nothing is seeded.

    Unlike `skills.root()` there is no first-run copy: a plugin is something a person chose to
    install, and shipping one would make "installed" mean two different things.
    """
    place = settings.plugins_dir()
    place.mkdir(parents=True, exist_ok=True)
    return place


# --------------------------------------------------------------------------- #
# The rows
# --------------------------------------------------------------------------- #


def rows(config_db: Path) -> dict[str, dict]:
    raw = config_store.load_settings(config_db).get(ROWS_KEY)
    try:
        loaded = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    except (TypeError, ValueError):
        # A corrupt blob reads as nothing rather than raising, the same answer
        # `mcp.manager.configured` gives — one bad write must not stop the app starting.
        return {}
    return {str(k): v for k, v in loaded.items() if isinstance(v, dict)}


def write_rows(config_db: Path, updated: dict[str, dict]) -> None:
    config_store.update_settings(config_db, {ROWS_KEY: json.dumps(updated)})
    forget_cache()


def row(config_db: Path, plugin_id: str) -> dict | None:
    return rows(config_db).get(plugin_id)


def patch_row(config_db: Path, plugin_id: str, changes: dict) -> dict:
    held = rows(config_db)
    if plugin_id not in held:
        raise PluginError(f"{plugin_id!r} is not installed.")
    held[plugin_id] = {**held[plugin_id], **changes}
    write_rows(config_db, held)
    return held[plugin_id]


def forget_cache() -> None:
    """Call on every write. A five-second stale enabled-set is a plugin that keeps contributing
    tools for five seconds after it was switched off, which reads as the switch not working."""
    global _cache
    with _lock:
        _cache = None


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #


def installed(config_db: Path) -> list[Plugin]:
    """Every plugin with a row and a readable folder, in install order."""
    found: list[Plugin] = []
    place = root()
    for plugin_id, held in rows(config_db).items():
        if held.get("retiredAt"):
            continue
        directory = place / plugin_id
        if not directory.is_dir():
            continue
        try:
            found.append(parse(directory))
        except PluginError as exc:
            # Printed rather than raised. One broken plugin must not cost the others — the
            # failure would otherwise read as plugins not existing at all.
            print(f"[kith] skipping plugin {plugin_id}: {exc}")
    return found


def get(config_db: Path, plugin_id: str) -> Plugin | None:
    return next((p for p in installed(config_db) if p.id == plugin_id), None)


def enabled(config_db: Path) -> list[Plugin]:
    held = rows(config_db)
    return [p for p in installed(config_db) if held.get(p.id, {}).get("enabled", True) and p.is_usable]


def enabled_ids(config_db: Path) -> frozenset[str]:
    """Cached, because this is asked on every chat request through the skills index."""
    global _cache
    with _lock:
        if _cache is not None and (time.monotonic() - _cache[0]) < _CACHE_SECONDS:
            return _cache[1]
    answer = frozenset(p.id for p in enabled(config_db))
    with _lock:
        _cache = (time.monotonic(), answer)
    return answer


def health(config_db: Path) -> list[dict]:
    """What is wrong, per plugin, in the four ways it can be wrong.

    A plugin has up to five independently-failing parts, and before this the person's
    diagnostic path for "it does nothing" was to guess. Four states:

    * ``broken`` — the manifest does not parse, or `problems()` is non-empty.
    * ``incompatible`` — a manifest version outside `SUPPORTED`. Keeps its row, its state and
      its grants and contributes nothing; **never auto-uninstalled**, because silently removing
      six tabs on an update is the single most likely real-world breakage here.
    * ``orphan`` — a row with no folder.
    * ``stray`` — a folder with no row. Contributes nothing by construction; reported so that
      "I dropped it in and nothing happened" has an answer on screen.
    """
    from kith.domain.plugins import SUPPORTED

    out: list[dict] = []
    place = root()
    held = rows(config_db)
    for plugin_id, record in held.items():
        if record.get("retiredAt"):
            continue
        directory = place / plugin_id
        if not directory.is_dir():
            out.append({"id": plugin_id, "kind": "orphan", "error": "Its folder is gone."})
            continue
        try:
            plugin = parse(directory)
        except PluginError as exc:
            out.append({"id": plugin_id, "kind": "broken", "error": str(exc)})
            continue
        if plugin.manifest_version not in SUPPORTED:
            out.append(
                {
                    "id": plugin_id,
                    "kind": "incompatible",
                    "error": (
                        f"{plugin.name} needs a different version of Kith. It is written for "
                        f"manifest {plugin.manifest_version}; this build accepts "
                        f"{', '.join(str(v) for v in SUPPORTED)}."
                    ),
                }
            )
            continue
        problems = plugin.problems()
        if problems:
            out.append({"id": plugin_id, "kind": "broken", "error": problems[0]})
    for directory in sorted(place.iterdir()) if place.is_dir() else []:
        if not directory.is_dir() or directory.name.startswith(".") or directory.name in held:
            continue
        out.append(
            {
                "id": directory.name,
                "kind": "stray",
                "error": "This folder is not installed, so nothing reads it. Install it to use it.",
            }
        )
    return out


# --------------------------------------------------------------------------- #
# The merged views
# --------------------------------------------------------------------------- #


def mcp_servers(config_db: Path) -> list[MCPServer]:
    """Enabled plugins' servers, as rows the MCP manager can reconcile against.

    **Merged rather than written into `mcp.servers` on install**, and that is the whole reason
    this function exists. A plugin row that lands in the persisted blob outlives its plugin: the
    settings page PUTs the whole list, so the row round-trips into the person's own
    configuration and nothing ever removes it again.

    The label is the plugin id. One string, four jobs — folder, id, label, tool namespace — so
    plugin-to-plugin label collision is structurally impossible and there is no
    declared-vs-resolved label concept anywhere.
    """
    from kith.infra import permissions

    held = rows(config_db)
    out: list[MCPServer] = []
    for plugin in enabled(config_db):
        if plugin.server is None:
            continue
        record = held.get(plugin.id, {})
        signature = spawn_signature(plugin)
        if not permissions.granted(signature):
            # Not started, not listed, not in any prompt. The decision was made before any turn
            # existed, so nothing can shrink the tools block mid-turn — and `connect_async` runs
            # on a daemon thread where a prompt would be drawn on nobody's screen.
            continue
        out.append(
            MCPServer(
                label=plugin.id,
                command=plugin.server.command,
                args=plugin.server.args,
                env={k: str(v) for k, v in (record.get("env") or {}).items()},
                enabled=True,
                owner=plugin.id,
            )
        )
    return out


def spawn_signature(plugin: Plugin) -> str:
    """What must be granted for this plugin's program to run.

    The **resolved** reach is hashed, not the manifest's spelling of it: `~/Notes` and
    `/Users/x/Notes` are one boundary, and two signatures for one boundary would re-ask for
    nothing. And the seal — whether this machine could confine at all — is a segment of its own,
    so an OS that loses `sandbox-exec` stops matching rather than quietly running unconfined
    under consent that was given for a confined program.
    """
    from kith.infra import confinement, permissions

    if plugin.server is None:
        return ""
    try:
        reach = confinement.resolve(plugin.server.reach, plugin.id).public()
        canonical = json.dumps(reach, sort_keys=True, separators=(",", ":"))
    except confinement.ConfinementError:
        # An unresolvable reach can never be granted, so it needs no stable signature — and
        # `problems()` is what says why, in words, at the review.
        return ""
    return permissions.spawn_signature(
        plugin.id,
        plugin.server.command,
        plugin.server.args,
        plugin.server.env_keys,
        seal="sealed" if confinement.available() else "open",
        reach=canonical,
    )


def command_signature(plugin_id: str, command: str) -> str:
    """What must be granted for one command to run.

    Three segments, so ``plugin:<id>:*`` covers it through `granted()`'s segment-wise
    containment while the four-segment spawn grant stays exact-match. Grant the plugin at the
    review, cover its commands; never the other way round, because a wildcard over a boundary
    would let a widened one inherit consent given for a narrower one.
    """
    return f"plugin:{plugin_id}:{command}"


def imported_skills(config_db: Path) -> dict[str, str]:
    """Skill name -> the plugin that put it in the person's folder.

    Replaces `skill_roots`, which handed `skills.roots()` a directory *inside* each plugin to
    read in place. Skills are copied at install now — see `install._import_skills` — so there is
    no second root to compose, and what is left to answer is provenance: which of the person's
    skills arrived with a plugin. That is what the collision check needs, so an upgrade does not
    refuse a plugin for clashing with the copy it made itself last time.
    """
    return {name: row.id for row in installed(config_db) for name in (row.skills or ())}


def owner_of_label(config_db: Path, label: str) -> str:
    """Which plugin contributes this MCP label, or "" for one the person configured."""
    return label if label in enabled_ids(config_db) else ""


def surfaces(config_db: Path) -> list[dict]:
    """Every enabled plugin's surfaces, for the index the interface reads at boot."""
    out: list[dict] = []
    for plugin in enabled(config_db):
        for surface in plugin.surfaces:
            out.append(
                {
                    "plugin": plugin.id,
                    "pluginName": plugin.name,
                    "view": surface.id,
                    "title": surface.title,
                    "icon": surface.icon,
                    # A browser pane is a different element entirely — see `plugin-web-view.tsx`
                    # — and the layout tree has to know before it paints, which is why this is
                    # in the inlined index rather than only in a mount answer.
                    "kind": surface.kind,
                    "home": surface.home,
                    "minWidth": surface.min_width,
                    "minHeight": surface.min_height,
                    "instances": surface.instances,
                    "answers": surface.answers,
                    "assets": list(surface.assets),
                    # What this tab may set off itself. The renderer refuses anything else a
                    # frame asks for, so this list *is* the bound.
                    "surfaceCommands": [one.name for one in plugin.commands if one.from_surface()],
                }
            )
    return out


def tool_schemas(config_db: Path) -> list[dict]:
    """The `plugin__…` function declarations, sorted.

    Sorted for the reason `mcp.manager.snapshot` gives: an unstable order is a different byte
    sequence, which is a cache miss on the whole prefix for no reason.
    """
    out: list[dict] = []
    for plugin in enabled(config_db):
        for command in plugin.commands:
            if command.model:
                out.append(command.schema(plugin.id, plugin.name))
    out.sort(key=lambda schema: schema["function"]["name"])
    return out


def installed_prompt_chars(config_db: Path) -> int:
    """What every installed plugin costs on every request, together.

    A per-plugin ceiling that ten plugins each satisfy while jointly doubling the prefix is not
    a ceiling. This is the number the Plugins screen shows and the number install checks against
    `MAX_INSTALLED_PROMPT_CHARS`.
    """
    return sum(p.prompt_chars() for p in enabled(config_db))
