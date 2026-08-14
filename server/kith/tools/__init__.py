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
from kith.services import custom_tools, touched, tuning
from kith.tools import (  # noqa: F401 - imported for their registration side effect
    asking,
    code,
    computer,
    identity,
    journal,
    memory,
    meta,
    notes,
    outreach,
    people,
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
from kith.tools.semantics import NEEDS_A_LANGUAGE_SERVER
from kith.tools.semantics import available as _language_server_available

__all__ = ["all_tools", "get", "host", "names", "run_tool", "tool_schemas"]


def tool_schemas(
    agent_db_path: Path | None = None,
    only: set[str] | None = None,
    mcp: list[dict] | None = None,
    language_server: bool | None = None,
) -> list[dict]:
    """The tool declarations to hand the model — built-ins plus, if a DB path is
    given, the tools Kith has built for himself, plus any MCP tools passed in.

    ``only`` scopes the set to a mode's relevant tools (fewer tokens, sharper
    focus). Custom tools are included only in the full set (when ``only`` is None).

    ``mcp`` is *passed in* rather than fetched, and that is deliberate. This function runs on
    every round, and asking the manager here would mean the block changing under a turn — a
    server dying, or being switched off in another tab, silently shrinks it, which changes
    the cached prefix and discards the whole prompt cache on the next round. The caller takes
    one snapshot per turn and hands the same list down. See `services/mcp/manager.snapshot`.

    ``language_server`` says whether the four semantic tools are worth their schema. They are
    the one group here that can be *categorically* unusable: on a machine with nothing
    installed every call answers "not installed", and about 700 characters of schema is
    carried on every round of every turn to make that possible. Resolved once per turn by the
    caller for the same cache reason as ``mcp`` — `None` means "do not filter", which is what
    every caller that has no opinion passes.
    """
    hide = set() if language_server is not False else set(NEEDS_A_LANGUAGE_SERVER)
    wanted = None if only is None else set(only) - hide
    builtins = [one for one in schemas(wanted) if one.get("function", {}).get("name") not in hide]
    extra = list(mcp or [])
    if agent_db_path is None or only is not None:
        return builtins + extra
    return builtins + custom_tools.schemas(agent_db_path) + extra


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
    if allow is not None and name not in allow:
        # Named rather than vague: he can act on "not in this mode" and cannot act on
        # "something went wrong". The list itself is not spelled out — on a 55-tool set that
        # is most of a round's budget spent telling him what he already had schemas for.
        return {
            "ok": False,
            "error": (
                f"`{name}` is not available in this part of the turn. Use one of the tools "
                "you were given, or finish with what you have."
            ),
        }
    entry = get(name)
    if entry is not None:
        try:
            answer = {"ok": True, "result": entry.run(agent_db_path, arguments or {})}
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

    if custom_tools.exists(agent_db_path, name):
        return custom_tools.run(agent_db_path, name, arguments or {})

    # MCP last, and that ordering is the collision policy. A built-in always wins by
    # construction rather than by a check someone could forget to write — a server shipping
    # a tool called `shell` simply cannot reach this line, because its name is
    # `mcp__<label>__shell` and nothing of ours is spelled that way.
    from kith.services.mcp import manager as mcp

    if mcp.owns(name):
        return mcp.run(name, arguments or {}, tuning.value("mcp_call_timeout"))

    return {"ok": False, "error": f"unknown tool: {name}.{_hint(name, agent_db_path)}"}


def _hint(name: str, agent_db_path: Path) -> str:
    """Point him at the real name.

    He invents plausible tool names — `run_command` for `shell`, `mark_task_as_doing`
    for `update_task` — and a bare "unknown tool" teaches him nothing, so he burns a
    round and often guesses wrong again. Aliases cover the cases where spelling
    distance cannot help (`run_command` and `shell` share no letters).
    """
    known = names() + [s["function"]["name"] for s in custom_tools.schemas(agent_db_path)]
    guess = suggest(name)
    near = [guess] if guess else difflib.get_close_matches(name, known, n=3, cutoff=0.45)
    if not near:
        return " Use one of the tools you were given."
    return f" Did you mean: {', '.join(near)}?"


def host(agent_db_path: Path, *, language_server: bool | None = None) -> ToolHost:
    """The tool layer as the loop needs it, with the per-turn answers already bound.

    `agent_db_path` and `language_server` are resolved once here rather than every round, for
    the reason `tool_schemas` documents at length: both are inputs to a block that is part of
    the cached prompt prefix, and one that changed mid-turn would discard the whole cache.
    Resolving them at the edge of the turn is what makes that structural instead of remembered.

    `language_server=None` means "ask" — a handful of `stat` calls, once. Pass a bool to skip
    even that, which is what a test with no language server on the machine wants.
    """
    available = language_server if language_server is not None else _language_server_available()
    return ToolHost(
        schemas=lambda only=None, mcp=None: tool_schemas(
            agent_db_path, only=only, mcp=mcp, language_server=available
        ),
        run=lambda name, arguments, allow=None: run_tool(name, arguments, agent_db_path, allow=allow),
    )
