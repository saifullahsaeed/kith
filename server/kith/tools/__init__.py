"""What Kith can do.

One module per area of his life, each declaring its tools with `@tool` so a schema
and its behaviour live together. Importing this package registers all of them — the
imports below are the registration, which is why they look unused.

Two entry points, and everything goes through them:

* ``tool_schemas`` — the declarations handed to the model each round.
* ``run_tool``     — execute one. The single chokepoint for every tool call, and so
  the place a permission layer belongs when this runs unsandboxed.
"""

from __future__ import annotations

import difflib
from pathlib import Path

from kith.domain.tooling import ToolHost
from kith.infra import permissions
from kith.services import touched, tuning
from kith.tools import (  # noqa: F401 - imported for their registration side effect
    aliases,
    asking,
    code,
    computer,
    delegation,
    journal,
    memory,
    outreach,
    plugins,
    projects,
    semantics,
    skills,
    sources,
    tasks,
    time,
    web,
)
from kith.tools.aliases import suggest
from kith.tools.registry import all_tools, get, names, schemas
from kith.tools.semantics import NEEDS_A_LANGUAGE_SERVER, OFFERED_WITHOUT_A_LANGUAGE_SERVER
from kith.tools.semantics import available as _language_server_available

__all__ = ["all_tools", "get", "host", "names", "run_tool", "tool_schemas"]


def tool_schemas(
    only: set[str] | None = None,
    mcp: list[dict] | None = None,
    language_server: bool | None = None,
    plugin_state: bool | None = None,
) -> list[dict]:
    """The tool declarations to hand the model — the built-ins, plus any MCP tools passed in.

    ``only`` scopes the set to a mode's relevant tools (fewer tokens, sharper focus).

    ``mcp`` is *passed in* rather than fetched, and that is deliberate. This function runs on
    every round, and asking the manager here would mean the block changing under a turn — a
    server dying, or being switched off in another tab, silently shrinks it, which changes
    the cached prefix and discards the whole prompt cache on the next round. The caller takes
    one snapshot per turn and hands the same list down. See `services/mcp/manager.snapshot`.

    ``language_server`` says whether `rename_symbol` is worth its schema. It is the one tool
    left here that can be *categorically* unusable: with nothing installed every call answers
    "not installed", and its schema is carried on every round of every turn to make that
    possible. It used to be four — `diagnostics`, `references` and `definition` were merged
    into `check_code` and `find_symbol`, which work either way and say which engine answered,
    so there is nothing to hide. Resolved once per turn by the caller for the same cache reason
    as ``mcp`` — `None` means "do not filter", which is what every caller that has no opinion
    passes.
    """
    # Two sets, moving in opposite directions on the same fact. With no server the tool that
    # needs one is hidden and the one that installs it is offered; with a server, the reverse.
    # `None` means nobody has an opinion, and nothing is hidden.
    hide: set[str] = set()
    if plugin_state is False:
        # No installed plugin keeps state, so the tool that reads it can only ever answer "no
        # plugin called that". Its schema is not free, and this is the difference between an
        # idle plugin costing nothing and costing a couple of hundred characters a round.
        hide.add("plugin_state")
    if language_server is False:
        hide |= set(NEEDS_A_LANGUAGE_SERVER)
    elif language_server is True:
        hide |= set(OFFERED_WITHOUT_A_LANGUAGE_SERVER)
    wanted = None if only is None else set(only) - hide
    builtins = [one for one in schemas(wanted) if one.get("function", {}).get("name") not in hide]
    return builtins + list(mcp or [])


def _any_plugin_holds_state() -> bool:
    """Does any enabled plugin declare a store? Wrapped, because a plugin fault must never
    decide the shape of the tools block — the safe answer is to offer the tool."""
    try:
        from kith import settings as live
        from kith.services.plugins import registry

        return any((plugin.state or {}) for plugin in registry.enabled(live.CONFIG_DB_PATH))
    except Exception:
        return True


def run_tool(name: str, arguments: dict, agent_db_path: Path, allow: set[str] | None = None) -> dict:
    """Execute a tool call. Always returns a dict, never raises — a failed tool
    should inform the model, not crash the stream.

    ``allow`` is the set of tools permitted in this context, and it is enforced *here*
    because here is where execution happens. It used to be enforced nowhere.

    Every allow-list in the codebase — the
    landing reserve that takes work tools away for the last rounds, the narrower set after a
    delegation — was only ever passed to `tool_schemas(only=...)`, which decides what the
    model is *shown*. This function resolved any name against the whole registry and ran it.
    Measured: in `breakout` mode, which offers six tools, calling `remember` and `add_task`
    both succeeded.

    So the lists were advisory, and a model that named a tool anyway — because the persona
    mentions it, because a skill it just read names it, because it saw the tool earlier in
    the same conversation — got it. The landing reserve exists to stop a turn gathering
    forever, and it could be ignored by simply calling `web_search` again.

    ``None`` means no restriction, which is the ordinary case and what every caller that
    does not scope its tools passes.
    """
    # Before the allow-list, because a retired name is not a different tool — it is this tool
    # under the name it used to have, and whether the *surviving* name is permitted here is the
    # question that matters. Checking first would refuse `edit_file` in a phase that allows
    # `edit_files`, which is the merge inventing a restriction nobody chose.
    # `get(name) is None` is the guard that matters: a retired name only redirects while it is
    # actually gone. Without it an entry written before its merge landed would hijack a tool
    # that still exists and still works — which is exactly what happened, and it took out a
    # fifth of the suite in one commit.
    gone = aliases.retired(name)
    if gone is not None and get(name) is None and get(gone.now) is not None:
        answer = run_tool(gone.now, aliases.translate(gone, arguments or {}), agent_db_path, allow)
        # Said in the result rather than kept quiet. The call worked, so this is not an error —
        # but a model that never hears the new name goes on paying a translation for ever, and
        # the note is what lets it stop.
        note = f"`{name}` is now `{gone.now}` — same job, so this ran as that."
        if isinstance(answer.get("result"), dict):
            existing = answer["result"].get("note")
            answer["result"] = {**answer["result"], "note": " ".join(filter(None, (existing, note)))}
        elif isinstance(answer.get("result"), str):
            answer["result"] = f"{answer['result']}\n\n[{note}]"
        elif not answer.get("ok"):
            answer["error"] = f"{answer.get('error', '')} ({note})".strip()
        return answer

    if allow is not None and name not in allow:
        # Named rather than vague: he can act on "not in this mode" and cannot act on
        # "something went wrong". The list itself is not spelled out — on a 49-tool set that
        # is most of a round's budget spent telling him what he already had schemas for.
        return {
            "ok": False,
            "error": (
                f"`{name}` is not available in this part of the turn. Use one of the tools "
                "you were given, or finish with what you have."
            ),
        }
    # MCP is resolved here rather than after the built-in branch, and that ordering is still
    # the collision policy: `entry` is consulted first, so a built-in always wins by
    # construction. A server shipping a tool called `shell` cannot reach the MCP arm, because
    # its name is `mcp__<label>__shell` and nothing of ours is spelled that way.
    from kith.services.mcp import manager as mcp
    from kith.services.plugins import commands as plugin_commands

    entry = get(name)
    mine = entry is not None
    if mine or plugin_commands.owns(name) or mcp.owns(name):
        try:
            if mine:
                answer = {"ok": True, "result": entry.run(agent_db_path, arguments or {})}
            elif plugin_commands.owns(name):
                answer = _run_plugin(name, arguments or {}, agent_db_path)
            else:
                answer = _run_mcp(mcp, name, arguments or {})
        except permissions.Denied as denied:
            # Not a failure — a question. The request rides along so the interface can put
            # an Allow button on this very tool result, instead of making someone hunt for
            # a settings page while he waits.
            #
            # Returns before the bookkeeping below on purpose: the call did not happen, so
            # recording that he has seen a version of the file would be a lie, and a lie that
            # actively suppresses the read he still needs to make.
            request = denied.decision.request
            return {
                "ok": False,
                "error": str(denied),
                "permission": request.public() if request else None,
            }
        except Exception as exc:
            answer = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        # After the call rather than before, so a write stamps the version it produced instead
        # of the one it replaced. A failure is recorded too — that he tried to open something
        # that is not there is the reason not to try again.
        #
        # The result goes with it because only the result knows how much of a file came back:
        # `read_file` windows to 400 lines by default, and the arguments of a complete read of
        # a short file are identical to those of a quarter read of a long one.
        touched.record(agent_db_path, name, arguments or {}, answer.get("result"))
        return answer

    return {"ok": False, "error": f"unknown tool: {name}.{_hint(name, agent_db_path)}"}


def _run_plugin(name: str, arguments: dict, agent_db_path: Path) -> dict:
    """One plugin command. Dispatched **above** the MCP arm and inside the same `try`.

    Above it because both namespaces are well-formed and only one of them is this: a command
    spelled `mcp__…__…` would be swallowed by the MCP arm and answered "that server is not
    connected", which is the reason `plugin__` exists as a separate prefix at all. Built-ins
    still win, because nothing built-in is spelled either way.
    """
    from kith import settings as live
    from kith.services.plugins import commands as plugin_commands

    return plugin_commands.run(agent_db_path, live.CONFIG_DB_PATH, name, arguments)


def _run_mcp(mcp, name: str, arguments: dict) -> dict:
    """One MCP tool call, gated.

    **This branch used to sit below the `try` above, and that was the whole bug.** A built-in
    tool's `permissions.Denied` is caught up there and turned into the `{ok, error, permission}`
    envelope the interface draws an Allow button on; `mcp.run` was called directly, so an MCP
    server was the one actor in the process that reached your disk with no gate in front of it
    and no row in `touched`. Moving the call inside that `try` is the fix — the gate below can
    raise, and the envelope, the Allow button and the `touched.record` all come for free from
    code that already existed.

    What is checked is the *program*, not the call. Kith cannot read an opaque `tools/call` and
    say which file it is about to open, so a per-call path check here would be a gate that
    inspects nothing while looking exactly like the ones that inspect everything. The honest
    unit is: may this program act for him at all. Confinement is what bounds *what* it can
    reach, and that is a separate piece of work with its own screen.

    The gate is above `mcp.run` rather than inside it so the wait for a person sits outside
    `mcp_call_timeout` (30s by default). Gate below it and every prompt becomes a timed-out
    call.
    """
    from kith import settings as live
    from kith.domain.mcp import split_tool_name

    config_db = live.CONFIG_DB_PATH
    label, tool = split_tool_name(name)
    signature = mcp.grant_signature(config_db, label)
    if signature:
        # Named by the plugin that contributes it where there is one, because "the 'circulars'
        # server" is a label out of a config file and "the Circular Watch plugin's helper" is
        # the thing the person actually installed. Composed here, in code, from the plugin's
        # own name rendered as data — never from a sentence a manifest supplied.
        owner = _plugin_named(config_db, label)
        who = f"{owner}'s helper program" if owner else f"the {label!r} server"
        # No signature means no configured row — the server was removed while a turn held its
        # snapshot. `mcp.run` already answers that readably, and refusing here instead would
        # replace a sentence he can act on with a prompt about a program that no longer exists.
        permissions.require_plugin(signature, f"{label}/{tool}" if tool else label, who)
    answer = mcp.run(name, arguments, tuning.value("mcp_call_timeout"))
    _absorb_state(config_db, label, answer)
    return answer


def _absorb_state(config_db, label: str, answer: dict) -> None:
    """A plugin's server says what it changed by putting it on its own tool result.

    No reverse channel, no second port, no per-install token injected into a subprocess
    environment — which would be a new credential kind to mint, store, revoke and leak into
    `ps -E` and into everything the subprocess spawns, all to reach a function this module can
    call on the line after `mcp.run` returns.

    `_kith_state` is reserved on an MCP result and taken off before the model sees it, so a
    server that returns that name for its own purposes cannot use it either way.

    **Swallowed on failure, and after the gate rather than before.** Bookkeeping must never
    fail the call it describes, and a refused call did not happen — recording that it did is
    the same lie this function's caller avoids by returning before `touched.record` on a denial.
    """
    held = answer.pop("_kith_state", None) if isinstance(answer, dict) else None
    if not isinstance(held, dict) or not held:
        return
    try:
        from kith import settings as live
        from kith.services.plugins import registry, state

        if not registry.owner_of_label(config_db, label):
            return
        state.write(live.AGENT_DB_PATH, label, held, writer="server")
    except Exception:
        pass


def _plugin_named(config_db, label: str) -> str:
    """The display name of the plugin contributing this label, or "" for a typed-in server."""
    try:
        from kith.services.plugins import registry as plugins

        if not plugins.owner_of_label(config_db, label):
            return ""
        plugin = plugins.get(config_db, label)
        return plugin.name if plugin else ""
    except Exception:
        return ""


def _hint(name: str, agent_db_path: Path) -> str:
    """Point him at the real name.

    He invents plausible tool names — `run_command` for `shell`, `mark_task_as_doing`
    for `update_task` — and a bare "unknown tool" teaches him nothing, so he burns a
    round and often guesses wrong again. Aliases cover the cases where spelling
    distance cannot help (`run_command` and `shell` share no letters).
    """
    known = names()
    guess = suggest(name)
    near = [guess] if guess else difflib.get_close_matches(name, known, n=3, cutoff=0.45)
    if not near:
        return " Use one of the tools you were given."
    return f" Did you mean: {', '.join(near)}?"


def dispatched(name: str) -> str:
    """The tool a name will actually run, which is what a policy keyed on names must ask.

    A registered name is itself. An unregistered one that `RETIRED` covers is the tool that
    took its job — so `fetch_url` answers `browse_page`, and the loop's `_PARALLEL_SAFE` and
    `_POLL_TOOLS` stop being blind to every retired spelling.
    """
    if get(name) is not None:
        return name
    gone = aliases.retired(name)
    return gone.now if gone else name


def host(
    agent_db_path: Path,
    *,
    language_server: bool | None = None,
    mcp: list[dict] | None = None,
    plugin: list[dict] | None = None,
) -> ToolHost:
    """The whole tool layer, frozen for one turn.

    Everything that can change between rounds is resolved here, once, and the reasons are the
    same reason: the tools block is part of the cached prompt prefix, so anything that shrank
    or grew mid-turn would discard the entire cache on the next round. That was documented on
    `tool_schemas` and left to each caller to honour; it is a property of this object now.

    * `mcp` — every MCP tool as it stands. Taken here when not supplied, so a caller cannot
      forget to snapshot and cannot take two snapshots that disagree. Held even when a server
      has since died: the *call* then fails with something readable, which costs one tool
      result rather than the whole prefix.
    * `language_server` — whether the four semantic tools are worth their schema. `None` asks,
      which is a handful of `stat` calls; pass a bool to skip even that.
    * `agent_db_path` — bound, so `run` has the database without every caller carrying it.

    The provenance sets come back on the host for the same reason: the turn used to rebuild
    them from a snapshot it was also holding, so two objects had an opinion about which tools
    existed and nothing made them agree.
    """
    if mcp is None:
        from kith.services.mcp import manager as mcp_manager

        mcp = mcp_manager.snapshot()
    available = language_server if language_server is not None else _language_server_available()
    # Whether `plugin_state` is worth its schema, resolved once here for the same cache reason
    # as everything else in this function.
    #
    # This is what makes "an installed but idle plugin costs nothing" true rather than nearly
    # true. A tool nobody can use still costs its declaration on every round of every turn, and
    # `language_server` above already established the shape: a tool that is *categorically*
    # unusable is hidden rather than offered so it can answer "not installed".
    holds_state = _any_plugin_holds_state()
    # Frozen once, beside the MCP snapshot, for the identical reason: a plugin enabled or
    # disabled in another tab mid-turn would otherwise change the tools block between rounds and
    # discard the whole prompt cache.
    if plugin is None:
        plugin = _plugin_snapshot()
    return ToolHost(
        schemas=lambda only=None: tool_schemas(
            only=only,
            mcp=(mcp or []) + (plugin or []),
            language_server=available,
            plugin_state=holds_state,
        ),
        dispatched=dispatched,
        run=lambda name, arguments, allow=None: run_tool(name, arguments, agent_db_path, allow=allow),
        mcp_names=frozenset(str(((one.get("function") or {}).get("name")) or "") for one in mcp),
        plugin_names=frozenset(str(((one.get("function") or {}).get("name")) or "") for one in plugin),
    )


def _plugin_snapshot() -> list[dict]:
    """Every enabled plugin's model-facing commands. Wrapped: a plugin fault must cost its own
    tools, never the whole tools block."""
    try:
        from kith import settings as live
        from kith.services.plugins import commands as plugin_commands

        return plugin_commands.snapshot(live.CONFIG_DB_PATH)
    except Exception as exc:
        print(f"[kith] plugins: could not read command schemas ({exc})")
        return []
