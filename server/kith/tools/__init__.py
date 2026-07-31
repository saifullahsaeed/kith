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

from kith.services import custom_tools, permissions
from kith.tools import (  # noqa: F401 - imported for their registration side effect
    computer,
    identity,
    journal,
    memory,
    meta,
    notes,
    outreach,
    people,
    projects,
    skills,
    sources,
    tasks,
    time,
    web,
)
from kith.tools.aliases import suggest
from kith.tools.registry import all_tools, get, names, schemas

__all__ = ["all_tools", "get", "names", "run_tool", "tool_schemas"]


def tool_schemas(agent_db_path: Path | None = None, only: set[str] | None = None) -> list[dict]:
    """The tool declarations to hand the model — built-ins plus, if a DB path is
    given, the tools Kith has built for himself.

    ``only`` scopes the set to a mode's relevant tools (fewer tokens, sharper
    focus). Custom tools are included only in the full set (when ``only`` is None).
    """
    builtins = schemas(only)
    if agent_db_path is None or only is not None:
        return builtins
    return builtins + custom_tools.schemas(agent_db_path)


def run_tool(name: str, arguments: dict, agent_db_path: Path) -> dict:
    """Execute a tool call. Always returns a dict, never raises — a failed tool
    should inform the model, not crash the stream."""
    entry = get(name)
    if entry is not None:
        try:
            return {"ok": True, "result": entry.run(agent_db_path, arguments or {})}
        except permissions.Denied as denied:
            # Not a failure — a question. The request rides along so the interface can put
            # an Allow button on this very tool result, instead of making someone hunt for
            # a settings page while he waits.
            request = denied.decision.request
            return {
                "ok": False,
                "error": str(denied),
                "permission": request.public() if request else None,
            }
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    if custom_tools.exists(agent_db_path, name):
        return custom_tools.run(agent_db_path, name, arguments or {})

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
