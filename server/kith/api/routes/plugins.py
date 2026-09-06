"""Plugins: look at one, install it, switch it, remove it.

The shape mirrors the MCP routes, including the rule that matters most there: **trying is not
saving.** `GET /api/plugins/review` parses a folder, validates it and prices what it would cost
on every request, and writes nothing at all — so reviewing a plugin cannot be how it gets
installed.

Every string a plugin contributes reaches the interface as *data* under its own key, never
folded into a sentence this app speaks. `permissions._refuse` is explicit that a justification
is "only ever supplied in code, never from anything a model composed"; a third party's sentence
on the screen where somebody grants disk access is that same mistake one layer out.
"""

from __future__ import annotations

from pathlib import Path

from flask import jsonify, request

from kith.api.blueprint import api
from kith.domain.plugins import PluginError
from kith.services.plugins import install as installer
from kith.services.plugins import registry
from kith.settings import CONFIG_DB_PATH


def _plugin_state_rows() -> list[dict]:
    """Per-plugin store diagnostics. Wrapped, because a read-only screen must not be the thing
    that a plugin fault takes down."""
    try:
        from kith import settings as live
        from kith.services.plugins import state

        return state.slots(live.AGENT_DB_PATH, CONFIG_DB_PATH)
    except Exception as exc:  # pragma: no cover - diagnostics are not worth a 500
        return [{"error": str(exc)}]


def _snapshot() -> dict:
    held = registry.rows(CONFIG_DB_PATH)
    plugins = registry.installed(CONFIG_DB_PATH)
    return {
        "root": str(registry.root()),
        "plugins": [
            {
                **plugin.public(),
                # The manifest says what is offered; the row says what was decided. Sent as two
                # keys rather than merged, so a screen cannot accidentally render a default as
                # though someone had chosen it.
                "decided": {
                    "enabled": held.get(plugin.id, {}).get("enabled", True),
                    "digest": held.get(plugin.id, {}).get("digest", False),
                    "envKeys": sorted(held.get(plugin.id, {}).get("env") or {}),
                    "installedAt": held.get(plugin.id, {}).get("installedAt", ""),
                    "version": held.get(plugin.id, {}).get("version", ""),
                },
            }
            for plugin in plugins
        ],
        "problems": registry.health(CONFIG_DB_PATH),
        # What each plugin is actually holding, and — the highest-value item in this block — the
        # digest line rendered verbatim. A plugin has up to five independently-failing parts, and
        # before this the diagnostic path for "it does nothing" was to guess. Seeing the line he
        # is actually given answers "why does he not know about my state" in one glance, where
        # every other signal only says that something was written.
        "state": _plugin_state_rows(),
        # The one figure that decides whether installing another plugin is free. Same shape the
        # Skills screen already uses for its index.
        "promptChars": registry.installed_prompt_chars(CONFIG_DB_PATH),
        "promptTokens": int(registry.installed_prompt_chars(CONFIG_DB_PATH) / 3.7),
        "promptLimit": installer.MAX_INSTALLED_PROMPT_CHARS,
    }


@api.get("/plugins")
@api.doc(
    summary="Installed plugins",
    description=(
        "What is installed, what each one offers, what the person decided about it, and what "
        "the whole set costs in every request. Environment names are reported and their values "
        "never are."
    ),
)
def list_plugins():
    return jsonify(_snapshot())


@api.get("/plugins/review")
@api.doc(
    summary="Read a folder as a plugin, without installing it",
    description=(
        "Parses and validates the manifest and reports what installing it would mean — the "
        "program it would run, the files that program would see, the skills it would add and "
        "what it would cost on every request. Writes nothing."
    ),
)
def review_plugin():
    source = str(request.args.get("path") or "").strip()
    if not source:
        return jsonify({"error": "send ?path=<folder>"}), 400
    try:
        return jsonify(installer.inspect(Path(source), CONFIG_DB_PATH))
    except PluginError as refused:
        return jsonify({"error": str(refused)}), 400


@api.post("/plugins")
@api.doc(
    summary="Install a plugin from a folder",
    description=(
        "Copies it in, grants its program the right to run, and records the decision. The grant "
        "is written here because this request IS the person's approval — the per-call gate then "
        "never prompts in ordinary use."
    ),
)
def install_plugin():
    payload = request.get_json(silent=True) or {}
    source = str(payload.get("path") or "").strip()
    if not source:
        return jsonify({"error": 'send {"path": "<folder>"}'}), 400
    supplied = payload.get("env")
    env = supplied if isinstance(supplied, dict) else {}
    try:
        plugin = installer.install(
            CONFIG_DB_PATH,
            Path(source),
            env={str(k): str(v) for k, v in env.items()},
            standing=bool(payload.get("standing", True)),
        )
    except PluginError as refused:
        return jsonify({"error": str(refused)}), 400
    return jsonify({"plugin": plugin.public(), **_snapshot()})


@api.patch("/plugins/<plugin_id>")
@api.doc(
    summary="Change what was decided about a plugin",
    description=(
        "`enabled` switches it without losing its configuration. `digest` is the only thing "
        "here that spends tokens on every turn forever, which is why a manifest can offer one "
        "and only a person can switch it on. `env` merges; an empty value removes."
    ),
)
def patch_plugin(plugin_id: str):
    payload = request.get_json(silent=True) or {}
    try:
        if "enabled" in payload:
            installer.set_enabled(CONFIG_DB_PATH, plugin_id, bool(payload["enabled"]))
        if "digest" in payload:
            installer.set_digest(CONFIG_DB_PATH, plugin_id, bool(payload["digest"]))
        supplied = payload.get("env")
        if isinstance(supplied, dict):
            installer.set_env(CONFIG_DB_PATH, plugin_id, supplied)
    except PluginError as refused:
        return jsonify({"error": str(refused)}), 400
    return jsonify(_snapshot())


@api.delete("/plugins/<plugin_id>")
@api.doc(
    summary="Remove a plugin",
    description=(
        "Removes the folder, stops its process and revokes what let it run. Its stored state is "
        "kept for thirty days unless `?data=delete` says otherwise, so a reinstall gets it back "
        "— the same bargain removing a skill strikes with the Trash."
    ),
)
def remove_plugin(plugin_id: str):
    delete_state = str(request.args.get("data") or "").lower() == "delete"
    try:
        installer.uninstall(CONFIG_DB_PATH, plugin_id, delete_state=delete_state)
    except PluginError as refused:
        return jsonify({"error": str(refused)}), 400
    return jsonify(_snapshot())


@api.get("/plugins/surfaces")
@api.doc(
    summary="Every enabled plugin's tabs",
    description=(
        "What the layout needs to draw a plugin tab: its title, icon and minimum width. The "
        "interface also receives this inline in the document it boots from, because the layout "
        "tree rehydrates from local storage at module import — before any fetch can resolve."
    ),
)
def list_plugin_surfaces():
    return jsonify({"surfaces": registry.surfaces(CONFIG_DB_PATH)})
