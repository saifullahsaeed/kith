"""Running one declared command, and the argument coercion that happens first.

A plugin declares commands as data. Core validates the arguments against the declaration,
gates the permission, dispatches, and — for the two deliveries that do not need a live frame —
performs the whole thing here.

**`delivery` is the field the design turns on.** `state` writes the plugin's store and `host`
asks the renderer for one of five effects, and **both work with the tab closed**. Only
`surface` needs a live frame, which is what bounds the one genuinely new primitive to the case
where it is semantically required: *do something to the thing the person is looking at*. With
the split, refusing a `surface` command for a backgrounded tab is correct rather than a
limitation — and mount-but-hide, four extra hidden iframe realms per window and a change to the
layout renderer for every user all disappear.

**The schema never shrinks because a tab closed.** A `surface` command is declared whether or
not its surface is open; if it is not, the *call* fails readably. Refuse the call, never the
schema — `mcp.manager.owns`'s lesson, and the only shape that keeps the tools block
byte-identical from the first round of a turn to the last.
"""

from __future__ import annotations

from pathlib import Path

from kith.domain.plugins import CommandDecl, Plugin, split_tool_name
from kith.kernel import session_context

#: Every refusal a command call can produce, phrased so he reports it and carries on rather
#: than stopping — the pattern `mcp.manager.run`'s "carry on without it" set established.
REFUSALS = {
    "plugin_gone": "The {plugin} plugin is not installed.",
    "no_such_command": "{plugin} has no command called {command!r}.",
    "bad_arguments": "{detail}",
    "surface_not_open": (
        "The {title} surface is not open, so there is nothing to ask. Say what you wanted to do "
        "with it, or use a command that does not need it."
    ),
    "surface_ambiguous": "There is more than one {title} open. Nothing was done.",
    "not_supported": (
        "{plugin}'s {command!r} needs a live surface, and this build cannot reach one yet. "
        "Carry on and say what you skipped."
    ),
    "no_renderer": "No window is open, so nothing could do that. Carry on and say what you skipped.",
}


def owns(name: str) -> bool:
    """Is this name a plugin command's? Decided by the namespace, not by what is installed.

    Claiming every well-formed `plugin__…__…` name is what makes the *refusal* accurate.
    `mcp.manager.owns` records the failure this avoids: a turn holds its tool snapshot after a
    plugin is disabled, so he will call a command whose plugin has gone, and falling through to
    "unknown tool" tells him he invented a tool he was handed two rounds ago — after which he
    stops trying anything from that plugin rather than routing around one failure.
    """
    plugin, command = split_tool_name(name)
    return bool(plugin and command)


def run(agent_db: Path, config_db: Path, name: str, arguments: dict) -> dict:
    """Dispatch one command call from the model, in the `{ok, result}` envelope the loop speaks."""
    plugin_id, command_name = split_tool_name(name)
    from kith.services.plugins import registry

    plugin = next((p for p in registry.enabled(config_db) if p.id == plugin_id), None)
    if plugin is None:
        return _refuse("plugin_gone", plugin=plugin_id)
    command = plugin.command(command_name)
    if command is None or not command.model:
        return _refuse("no_such_command", plugin=plugin.name, command=command_name)

    coerced, wrong = coerce(command, arguments or {})
    if wrong:
        return _refuse("bad_arguments", detail=f"{plugin.name}'s {command.title!r} {wrong}")

    return dispatch(agent_db, config_db, plugin, command, coerced, origin="model")


def dispatch(
    agent_db: Path,
    config_db: Path,
    plugin: Plugin,
    command: CommandDecl,
    args: dict,
    *,
    origin: str = "model",
) -> dict:
    """Perform one command whose arguments are already coerced.

    `origin` is "model" or "person". **A person clicking a button core drew IS the
    authorisation**, so that origin skips the gate — and that is only safe while core actually
    draws every affordance, which is why the frame has no action channel and why adding one
    later would collapse this gate to `origin="person"` asserted by an untrusted page.
    """
    from kith.infra import permissions
    from kith.services.plugins import registry

    if origin != "person":
        # Never prompts in ordinary use: the install review wrote `plugin:<id>:*`, which covers
        # this through `granted()`'s segment-wise containment. It exists for a revocation landing
        # mid-turn, and so that a refusal carries the envelope the Allow button draws on.
        permissions.require_plugin(
            registry.command_signature(plugin.id, command.name),
            f"{plugin.id}/{command.name}",
            f"the {plugin.name} plugin",
        )

    if command.delivery == "state":
        return _write_state(agent_db, plugin, command, args)
    if command.delivery == "server":
        return _ask_server(agent_db, config_db, plugin, command, args)
    if command.delivery == "host":
        return _ask_renderer(plugin, command, args)
    return _ask_surface(plugin, command, args)


def _write_state(agent_db: Path, plugin: Plugin, command: CommandDecl, args: dict) -> dict:
    """The whole of `state` delivery: write the declared keys and stop.

    Inline, no wait, no table of pending calls, and it works with every window shut.

    **Two shapes, and both name their keys in the manifest.** `does.set` maps parameters onto
    keys of the same name — the right thing for a command that sets a value, like a title or a
    filter. `does.collect` puts the *whole call* under one key, which is what an append needs:
    "add a shape" is one record with seven fields, and a surface reading that key can fold it
    into a list of its own and hand the list back. Neither lets a command reach a key it did not
    declare by passing an extra parameter.
    """
    from kith.services.plugins import state

    collect = command.does.get("collect")
    if isinstance(collect, str) and collect:
        # Stamped so a surface can tell a new call from one it has already folded in. A counter
        # rather than a clock: `Date.now()` in the renderer and a server clock here would give
        # two different answers about the same call, and the surface would fold one twice.
        values = {collect: {"command": command.name, "args": args}}
        return _collect(agent_db, plugin, collect, values[collect])

    wanted = [str(key) for key in (command.does.get("set") or [])]
    values = {key: args[key] for key in wanted if key in args}
    if not values:
        return {"ok": True, "result": {"changed": {}, "note": "nothing to set"}}
    try:
        written = state.write(agent_db, plugin.id, values, writer="host")
    except state.PluginStateError as refused:
        return {"ok": False, "error": str(refused)}
    return {"ok": True, "result": {"from": plugin.id, "changed": values, "slot": written["slot"]}}


#: How many un-drained records a collected key may hold.
#:
#: Generous against any real burst — he drew seventeen shapes in one turn and that is the most
#: anyone has done — and bounded so a surface that never drains cannot grow the slot until the
#: byte cap refuses an unrelated write.
MAX_QUEUED = 64


def _collect(agent_db: Path, plugin: Plugin, key: str, record: dict) -> dict:
    """Append one call's whole record to `key`, with a sequence number on it.

    **A queue, not a slot, and that distinction was a real bug.** This wrote `{**record, seq}`
    over the key, so each call replaced the last. A surface only folds a record when the host
    pushes the store to it — so seventeen `draw` calls produced *one* shape: the frame was told
    once, saw only the newest record, folded that, and the other sixteen had already been
    overwritten by the calls that followed them. Measured exactly that way: a title that was
    right, and "1 shape".

    Appending makes the fold independent of whether anything is listening. The tab can be shut
    for the whole turn and still show every shape when it opens, which is the property a store
    is *for* — and the `seq` a surface tracks turns "what is new" into a comparison rather than
    a guess about timing.
    """
    from kith.services.plugins import state

    try:
        held = state.read(agent_db, plugin.id, [key])["values"].get(key)
        queued = list(held) if isinstance(held, list) else []
        # A dict is what the previous shape of this function wrote. Carried over rather than
        # discarded, so a plugin installed before this change keeps whatever it was holding.
        if isinstance(held, dict):
            queued = [held]
        seq = max((int(one.get("seq", 0)) for one in queued if isinstance(one, dict)), default=0) + 1
        queued.append({**record, "seq": seq})
        written = state.write(agent_db, plugin.id, {key: queued[-MAX_QUEUED:]}, writer="host")
    except state.PluginStateError as refused:
        return {"ok": False, "error": str(refused)}
    return {
        "ok": True,
        "result": {"from": plugin.id, "queued": record, "at": seq, "slot": written["slot"]},
    }


def _ask_server(agent_db: Path, config_db: Path, plugin: Plugin, command: CommandDecl, args: dict) -> dict:
    """`server` delivery — the plugin's own subprocess performs it.

    **The delivery that makes a surface driveable.** Everything a plugin's server can do reaches
    the model as an `mcp__<plugin>__<tool>` call, and only the model can make one. So a browser
    plugin's tab could show you a page and had no way to let you type an address into it: the
    person's own click had nowhere to go.

    This routes a declared command onto one of that plugin's own tools. Nothing new is granted —
    the server is already running under a grant a person gave at install, and this calls a tool
    it already offers. What is new is *who* may set it off.
    """
    from kith.services.mcp import manager
    from kith.services.plugins import registry

    tool = str(command.does.get("tool") or "")
    if not tool:
        return _refuse("no_such_command", plugin=plugin.name, command=command.name)
    if plugin.id not in {row.label for row in registry.mcp_servers(config_db)}:
        return _refuse("plugin_gone", plugin=plugin.name)

    from kith.domain.mcp import tool_name
    from kith.services import tuning

    answer = manager.run(tool_name(plugin.id, tool), args, float(tuning.value("mcp_call_timeout")))
    # The reserved key is lifted here as well as in `run_tool`, so a command a *person* set off
    # updates the surface exactly as one the model made does. Without it a click would work and
    # the tab would not move, which reads as the click having missed.
    held = answer.pop("_kith_state", None) if isinstance(answer, dict) else None
    if isinstance(held, dict) and held:
        from kith.services.plugins import state

        try:
            state.write(agent_db, plugin.id, held, writer="server")
        except state.PluginStateError as refused:
            print(f"[kith] plugins: {plugin.id} could not record what it changed ({refused})")
    return answer


def _ask_renderer(plugin: Plugin, command: CommandDecl, args: dict) -> dict:
    """`host` delivery — one of five effects, performed by the renderer.

    Not built in this tranche, and refused rather than silently succeeding. The effects run in
    the renderer because the one that matters most, folding a conversation, has its whole
    implementation inside a rank-5 route that a service cannot import; so this needs the
    call-parking machinery that `surface` delivery needs, and both arrive together.
    """
    if session_context.unattended():
        return _refuse("no_renderer")
    return _refuse("not_supported", plugin=plugin.name, command=command.name)


def _ask_surface(plugin: Plugin, command: CommandDecl, args: dict) -> dict:
    """`surface` delivery — the call is routed into the live frame and awaited.

    **Refuse the call, never the schema.** A `surface` command is declared whether or not its
    surface is open, because a turn holds its tool snapshot from its first round to its last —
    so shrinking the block when a tab closes would discard the whole prompt cache, and answering
    "unknown tool" would tell him he invented something he was handed two rounds ago. What
    changes when the tab is shut is that the *call* says so, in a sentence he can act on.

    A pane renders only its active tab, so a surface sitting behind another tab in the same pane
    is genuinely unmounted and gets the same answer. That is correct rather than a limitation: a
    `surface` command means "do something to the thing the person is looking at".
    """
    from kith.services.plugins import calls, documents

    surface = plugin.surface(command.surface)
    title = surface.title if surface else command.surface
    conversation = session_context.current()
    mounts = documents.live(plugin.id, command.surface, conversation=conversation)
    if not mounts:
        return _refuse("surface_not_open", title=title)
    if len({one.instance for one in mounts}) > 1:
        # Naming them beats picking one: picking is how a person ends up watching a command act
        # somewhere they are not looking.
        return _refuse("surface_ambiguous", title=title)

    return calls.ask(
        calls.new(
            conversation,
            plugin.id,
            command.name,
            command.surface,
            args,
            instance=mounts[0].instance,
            repeatable=command.repeatable,
            timeout_ms=command.timeout_ms,
        )
    )


def _refuse(code: str, **fields) -> dict:
    return {"ok": False, "error": REFUSALS[code].format(**fields), "code": code}


# --------------------------------------------------------------------------- #
# Arguments
# --------------------------------------------------------------------------- #


def coerce(command: CommandDecl, arguments: dict) -> tuple[dict, str]:
    """Bring one call's arguments to the declared shape. Returns `(coerced, "")` or `({}, why)`.

    Unknown keys are dropped, strings capped, enums enforced, numbers clamped. A model that
    passes garbage gets a **named** refusal rather than a plugin receiving garbage — which is
    the difference between one wasted round and a surface in a state its author never wrote code
    for.
    """
    out: dict = {}
    for key, shape in command.params.items():
        if key not in arguments:
            continue
        value = arguments[key]
        kind = shape.get("type")
        if kind == "string":
            text = " ".join(str(value).split()) if not isinstance(value, str) else value
            choices = shape.get("enum")
            if choices and text not in choices:
                return {}, f"needs {key!r} to be one of: {', '.join(str(c) for c in choices)}."
            out[key] = text[: int(shape.get("maxLength") or 200)]
        elif kind in ("number", "integer"):
            try:
                number = float(value)
            except (TypeError, ValueError):
                return {}, f"needs {key!r} to be a number."
            if "minimum" in shape:
                number = max(float(shape["minimum"]), number)
            if "maximum" in shape:
                number = min(float(shape["maximum"]), number)
            out[key] = int(number) if kind == "integer" else number
        elif kind == "boolean":
            out[key] = bool(value) if not isinstance(value, str) else value.lower() in ("1", "true", "yes")
    missing = [key for key in command.required if key not in out]
    if missing:
        return {}, f"needs {', '.join(repr(key) for key in missing)}."
    return out, ""


def snapshot(config_db: Path) -> list[dict]:
    """Every enabled plugin's model-facing commands, as function schemas, sorted.

    Sorted for the reason `mcp.manager.snapshot` gives: an unstable order is a different byte
    sequence, which is a cache miss on the whole prefix for no reason.
    """
    from kith.services.plugins import registry

    return registry.tool_schemas(config_db)


def shape_reply(command: CommandDecl, value: dict) -> dict:
    """One surface's answer, brought to the shape its command declared.

    **This is what stops an RPC reply becoming a prose channel into a turn.** A reply is text
    written by a third party inside a frame that was sealed because it is not trusted, and it
    lands in a tool result the model reads. So there is no field for prose unless the manifest
    declared one and said how long it may be: fields not in `returns` are dropped, strings are
    cut to their declared `maxLength`, numbers are clamped, and anything that is not a primitive
    goes — structure being how you would smuggle a paragraph in dressed as a label.

    Run here as well as in the renderer, which is the deliberate duplication `prompt.py` insists
    on: the renderer's check ran on the far side of an HTTP request anything local can make.
    """
    out: dict = {}
    for key, shape in (command.returns or {}).items():
        if key not in value:
            continue
        held = value[key]
        kind = shape.get("type")
        if kind == "string":
            out[key] = " ".join(str(held).split())[: int(shape.get("maxLength") or 200)]
        elif kind in ("number", "integer"):
            try:
                number = float(held)
            except (TypeError, ValueError):
                continue
            if "minimum" in shape:
                number = max(float(shape["minimum"]), number)
            if "maximum" in shape:
                number = min(float(shape["maximum"]), number)
            out[key] = int(number) if kind == "integer" else number
        elif kind == "boolean":
            out[key] = bool(held)
    return out
