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
from kith.services.plugins import icons, registry
from kith.services.plugins import install as installer
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


# --------------------------------------------------------------------------- #
# Surfaces: mounting, serving, and what a frame says back
# --------------------------------------------------------------------------- #


@api.post("/plugins/<plugin_id>/surface/<view>/mount")
@api.doc(
    summary="Claim a ticket for one frame",
    description=(
        "Returns a single-use URL for the sealed document. A ticket rather than a stable path, "
        "because a frame's `src` cannot carry the API token and a guessable open path would "
        "drop the unguessable-id property that exemption rests on."
    ),
)
def mount_surface(plugin_id: str, view: str):
    from kith.services.plugins import documents

    payload = request.get_json(silent=True) or {}
    plugin = registry.get(CONFIG_DB_PATH, plugin_id)
    if plugin is None or plugin_id not in registry.enabled_ids(CONFIG_DB_PATH):
        return jsonify({"error": f"{plugin_id!r} is not installed or is switched off."}), 404
    surface = plugin.surface(view)
    if surface is None:
        return jsonify({"error": f"{plugin.name} has no {view!r} surface."}), 404
    try:
        entry = documents.mount(
            plugin_id,
            view,
            instance=str(payload.get("instance") or ""),
            client=str(payload.get("client") or ""),
            conversation=str(payload.get("conversation") or ""),
            tab=str(payload.get("tab") or ""),
        )
        entry.document = documents.sealed(
            plugin, surface, _palette(payload.get("theme")), icons.named(_icon_names(plugin))
        )
    except PluginError as refused:
        return jsonify({"error": str(refused)}), 400
    return jsonify(
        {
            "ticket": entry.ticket,
            "url": f"/api/plugins/frame/{entry.ticket}",
            "protocol": 1,
            "title": surface.title,
            "minWidth": surface.min_width,
        }
    )


@api.get("/plugins/frame/<ticket>")
@api.doc(
    summary="Serve a mounted surface",
    description=(
        "The sealed document, with the same policy a canvas carries. Every asset is already "
        "inlined, so nothing here fetches anything."
    ),
)
def serve_frame(ticket: str):
    from flask import Response

    from kith.domain.seal import POLICY
    from kith.services.plugins import documents

    entry = documents.held(ticket)
    if entry is None or not entry.document:
        return jsonify({"error": "that surface is not mounted"}), 404
    # The frame has said nothing yet, but fetching its own document is the only thing that
    # happens between mounting and `ready`, so this is where the mount becomes answerable.
    entry.ready = True
    response = Response(entry.document, mimetype="text/html")
    # `setdefault` elsewhere means this policy is the one that survives; without it the
    # document would inherit the app's, which permits `script-src 'self'`.
    response.headers["Content-Security-Policy"] = POLICY
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


@api.delete("/plugins/frame/<ticket>")
@api.doc(summary="Release a mounted surface", description="Called when a plugin tab unmounts.")
def release_frame(ticket: str):
    from kith.services.plugins import documents

    documents.unmount(ticket)
    return jsonify({"ok": True})


@api.post("/plugins/frame/<ticket>/state")
@api.doc(
    summary="What a surface changed",
    description=(
        "A sealed frame cannot reach the API — it has no network and no origin — so it asks the "
        "renderer, which asks this. **Which plugin is writing comes from the ticket, never from "
        "the body**: identity before content, applied to a namespace instead of a payload."
    ),
)
def write_surface_state(ticket: str):
    from kith.kernel import session_context
    from kith.services.plugins import documents, state
    from kith.settings import AGENT_DB_PATH

    entry = documents.held(ticket)
    if entry is None:
        return jsonify({"error": "that surface is not mounted"}), 404
    payload = request.get_json(silent=True) or {}
    values = payload.get("values")
    if not isinstance(values, dict):
        return jsonify({"error": 'send {"values": {...}}'}), 400
    supplied = payload.get("expect")
    expect = {str(k): int(v) for k, v in supplied.items()} if isinstance(supplied, dict) else None
    # The slot comes from the mount's conversation, and the write runs inside that context so
    # `state._owner` resolves it the one way it is allowed to be resolved.
    try:
        with session_context.working_in(entry.conversation):
            written = state.write(AGENT_DB_PATH, entry.plugin, values, writer="surface", expect=expect)
    except state.PluginStateError as refused:
        return jsonify({"error": str(refused)}), 400
    return jsonify(written)


@api.post("/plugins/frame/<ticket>/drop")
@api.doc(summary="Keys a surface no longer needs")
def drop_surface_state(ticket: str):
    from kith.kernel import session_context
    from kith.services.plugins import documents, state
    from kith.settings import AGENT_DB_PATH

    entry = documents.held(ticket)
    if entry is None:
        return jsonify({"error": "that surface is not mounted"}), 404
    keys = (request.get_json(silent=True) or {}).get("keys")
    if not isinstance(keys, list):
        return jsonify({"error": 'send {"keys": [...]}'}), 400
    try:
        with session_context.working_in(entry.conversation):
            gone = state.drop(AGENT_DB_PATH, entry.plugin, [str(k) for k in keys])
    except state.PluginStateError as refused:
        return jsonify({"error": str(refused)}), 400
    return jsonify({"dropped": gone})


@api.get("/plugins/<plugin_id>/state")
@api.doc(
    summary="What a plugin is holding, for its surface to render",
    description="Scoped to the conversation the caller names, verified against the plugin's own scope.",
)
def read_plugin_state(plugin_id: str):
    from kith.kernel import session_context
    from kith.services.plugins import state
    from kith.settings import AGENT_DB_PATH

    conversation = str(request.args.get("conversation") or "")
    try:
        with session_context.working_in(conversation):
            return jsonify(state.read(AGENT_DB_PATH, plugin_id))
    except state.PluginStateError as refused:
        return jsonify({"error": str(refused)}), 400


@api.post("/plugins/<plugin_id>/command/<command>")
@api.doc(
    summary="Run a declared command as the person",
    description=(
        "For a button Kith drew in its own chrome. A person clicking one IS the authorisation, "
        "so this origin skips the permission gate — which is only safe while core draws every "
        "affordance, and is why a sealed frame has no action channel of its own."
    ),
)
def run_plugin_command(plugin_id: str, command: str):
    from kith.kernel import session_context
    from kith.services.plugins import commands
    from kith.settings import AGENT_DB_PATH

    plugin = registry.get(CONFIG_DB_PATH, plugin_id)
    if plugin is None:
        return jsonify({"error": f"{plugin_id!r} is not installed."}), 404
    declared = plugin.command(command)
    if declared is None:
        return jsonify({"error": f"{plugin.name} has no {command!r} command."}), 404
    payload = request.get_json(silent=True) or {}
    args, wrong = commands.coerce(declared, payload.get("args") or {})
    if wrong:
        return jsonify({"error": f"{declared.title!r} {wrong}"}), 400
    with session_context.working_in(str(payload.get("conversation") or "")):
        answer = commands.dispatch(AGENT_DB_PATH, CONFIG_DB_PATH, plugin, declared, args, origin="person")
    return jsonify(answer)


def _icon_names(plugin) -> list[str]:
    """Every icon this plugin named, across its surfaces and its buttons."""
    found = [surface.icon for surface in plugin.surfaces]
    found += [str(command.present.get("icon") or "") for command in plugin.commands]
    return [name for name in found if name]


def _palette(theme) -> dict:
    """The tokens a surface starts from.

    Supplied by the renderer, which is the only place that knows whether the person is in light
    or dark — and passed through `canvasTokens`-shaped names so one document and one live theme
    message cannot drift, which is the bug that comment in `canvas-bridge.ts` exists for.
    """
    if isinstance(theme, dict) and theme:
        return {str(k): str(v) for k, v in theme.items()}
    return {
        "bg": "#faf9f7",
        "line": "#e4e1dc",
        "text": "#1c1a17",
        "dim": "#6b6660",
        "accent": "#b4703a",
        "accent-soft": "#f0e2d5",
        "second": "#3a6b58",
        "second-soft": "#dceade",
        "muted": "#f2f0ed",
    }


# --------------------------------------------------------------------------- #
# Two-way: what a surface is being asked, and what it hands back
# --------------------------------------------------------------------------- #


@api.get("/plugins/calls")
@api.doc(
    summary="What a surface is being asked right now",
    description=(
        "Snapshot-then-subscribe, the same join `use-activity` uses. A renderer fetches this on "
        "mount **and** on a `plugin_call` change — so a call survives a reload rather than being "
        "lost with a push that had already happened. Handing one out claims it for that "
        "renderer, so two windows on one backend do not both deliver it and race to answer."
    ),
)
def list_plugin_calls():
    from kith.services.plugins import calls

    return jsonify(
        {
            "calls": calls.pending(
                str(request.args.get("conversation") or ""),
                str(request.args.get("client") or ""),
            )
        }
    )


@api.post("/plugins/calls/<call_id>/reply")
@api.doc(
    summary="A surface's answer",
    description=(
        "Validated against the command's declared `returns` **again** here, after the renderer "
        "has already done it — the deliberate duplication the prompt builder insists on, because "
        "the first check ran on the far side of an HTTP request anything local can make. It is "
        "what stops an RPC reply becoming a prose channel into a turn."
    ),
)
def reply_to_plugin_call(call_id: str):
    from kith.services.plugins import calls, commands

    payload = request.get_json(silent=True) or {}
    supplied = payload.get("value")
    value = supplied if isinstance(supplied, dict) else {}
    ok = bool(payload.get("ok", True))

    held = calls.held(call_id)
    if held is None:
        # Not an error: a late reply to a call that has already timed out is the ordinary shape
        # of a slow frame, and the renderer has nothing useful to do about it.
        return jsonify({"accepted": False, "note": "that call is no longer open"})

    plugin = registry.get(CONFIG_DB_PATH, held.plugin)
    declared = plugin.command(held.command) if plugin else None
    shaped = commands.shape_reply(declared, value) if declared else {}
    return jsonify({"accepted": calls.reply(call_id, shaped, ok=ok)})


@api.post("/plugins/frame/<ticket>/file")
@api.doc(
    summary="A file a surface produced",
    description=(
        "The channel a surface has for handing bytes back — a rendered image, an export. The "
        "store cannot carry them (a value caps at 8 KB, because it feeds the prompt) and the "
        "frame has no network. Written into the plugin's own storage, which is inside its "
        "confinement boundary, and the reply is the path the model reads it by."
    ),
)
def put_surface_file(ticket: str):
    from kith.services.plugins import documents, state

    entry = documents.held(ticket)
    if entry is None:
        return jsonify({"error": "that surface is not mounted"}), 404
    blob = request.get_data(cache=False)
    # Which plugin is writing comes from the ticket, never the body: identity before content,
    # applied to a namespace instead of a payload.
    try:
        written = state.put_file(
            entry.plugin,
            str(request.args.get("name") or ""),
            request.headers.get("Content-Type", ""),
            blob,
        )
    except state.PluginStateError as refused:
        return jsonify({"error": str(refused)}), 400
    return jsonify(written)


@api.get("/plugins/<plugin_id>/file")
@api.doc(
    summary="A file the plugin holds, for its own surface to show",
    description=(
        "The other direction. A surface cannot fetch anything — `default-src 'none'` — so a "
        "screenshot its server took cannot be displayed by asking for it. The renderer reads it "
        "here and hands the bytes over the bridge, where the frame turns them into a `blob:` "
        "URL; the seal already permits `img-src blob:`. Confined to the plugin's own storage."
    ),
)
def get_plugin_file(plugin_id: str):
    from flask import Response

    from kith.infra import confinement

    wanted = str(request.args.get("path") or "").strip()
    if not wanted:
        return jsonify({"error": "send ?path="}), 400
    root = (confinement.home_for(plugin_id) / "files").resolve()
    # Resolve first, then confirm containment. The other order lets `../` walk out, and this
    # path arrived over HTTP.
    target = Path(wanted).expanduser().resolve()
    if root not in target.parents or not target.is_file():
        return jsonify({"error": "that file is not one of this plugin's"}), 404
    if target.stat().st_size > state_max_file_bytes():
        return jsonify({"error": "that file is too large to hand to a surface"}), 413
    response = Response(target.read_bytes(), mimetype=_mime_of(target))
    response.headers["Cache-Control"] = "no-store"
    return response


def state_max_file_bytes() -> int:
    from kith.services.plugins import state

    return state.MAX_FILE_BYTES


def _mime_of(target: Path) -> str:
    from kith.services.plugins import state

    for mime, suffix in state.FILE_KINDS.items():
        if target.suffix.lower() == suffix:
            return mime
    return "application/octet-stream"
