"""Tools that need a language server, and are honest when there isn't one.

Every one of these can fail for a reason that is nobody's mistake — nothing installed, a
server that will not start, a symbol that is not in the file. All of those come back as a
readable result rather than an exception, because an exception ends the round and a sentence
lets him try the next thing. `outline` and `grep` are always the next thing, and the messages
say so.
"""

from __future__ import annotations

from pathlib import Path

from kith.services.lsp import semantics as service
from kith.services.lsp.manager import Unavailable, manager
from kith.tools.params import INT, STR
from kith.tools.registry import tool

#: The tools in this module. `tool_schemas` consults this to leave them out of the prompt
#: entirely when no language server can serve the folder being worked in — a schema costs
#: tokens on every round, and advertising a capability that will always answer "not
#: installed" is exactly the waste the toolset lists exist to prevent.
NEEDS_A_LANGUAGE_SERVER = frozenset({"diagnostics", "references", "definition", "rename_symbol"})

_SYMBOL = {**STR, "description": "The name to ask about, exactly as it is written in the code."}
_NEAR = {
    **INT,
    "description": "Line to disambiguate by, if the name appears more than once (optional).",
}


def _resolved(raw: str) -> Path:
    from kith.infra import permissions
    from kith.infra import workspace as sandbox

    target = Path(sandbox.resolve(raw))
    permissions.require_path("read", target, sandbox.root())
    return target


def _guarded(call, *args, **kwargs):
    """Run a semantic call, turning the expected failures into readable results."""
    from kith.services.lsp.client import LSPError

    try:
        return call(*args, **kwargs)
    except Unavailable as exc:
        return {"unavailable": str(exc)}
    except LSPError as exc:
        return {"error": f"the language server failed: {exc}"}
    except OSError as exc:
        return {"error": str(exc)}


@tool(
    "diagnostics",
    "What is wrong with one file, right now — type errors, undefined names, unused imports — "
    "answered by the language server in milliseconds. Use this after editing a file, rather "
    "than waiting to run check_code over the whole project at the end: it is per-file and "
    "immediate, so a mistake is caught while you still remember making it. check_code is "
    "still the thing to run before you call a job done.",
    {"path": {**STR, "description": "The file to check."}},
    required=("path",),
)
def diagnostics(path: Path, args: dict):
    target = _resolved(args["path"])
    return _guarded(service.diagnostics, target)


@tool(
    "references",
    "Every place a name is actually used — exactly, from the language server, not from a text "
    "search. Use it before you change or delete anything shared: grep matches the word in "
    "comments and strings and misses re-exports and aliased imports, so 'no matches' from "
    "grep is not evidence that nothing calls it. Give the file the name appears in and the "
    "name; add `near_line` if it appears several times in that file.",
    {"path": STR, "symbol": _SYMBOL, "near_line": _NEAR},
    required=("path", "symbol"),
)
def references(path: Path, args: dict):
    target = _resolved(args["path"])
    return _guarded(service.references, target, str(args["symbol"]), int(args.get("near_line") or 0))


@tool(
    "definition",
    "Where a name comes from — the file and line it is defined on. Faster and surer than "
    "guessing at a filename and grepping for it, and it follows imports across the project.",
    {"path": STR, "symbol": _SYMBOL, "near_line": _NEAR},
    required=("path", "symbol"),
)
def definition(path: Path, args: dict):
    target = _resolved(args["path"])
    return _guarded(service.definition, target, str(args["symbol"]), int(args.get("near_line") or 0))


@tool(
    "rename_symbol",
    "Rename something everywhere it is used, and nowhere it merely appears. This is the right "
    "way to rename anything shared: the language server knows the difference between a use of "
    "a name and the same letters in a comment, a string, or a different scope — a search and "
    "replace does not, and gets both wrong. It edits the files itself and tells you which. "
    "Check `changes` afterwards, as you would for any edit you did not read line by line.",
    {
        "path": STR,
        "symbol": _SYMBOL,
        "new_name": {**STR, "description": "What to call it instead."},
        "near_line": _NEAR,
    },
    required=("path", "symbol", "new_name"),
)
def rename_symbol(path: Path, args: dict):
    from kith.infra import permissions
    from kith.infra import workspace as sandbox

    target = Path(sandbox.resolve(args["path"]))
    # A rename writes, and it writes to files other than this one. The gate has to be the
    # write gate, not the read gate that the other three use.
    permissions.require_path("write", target, sandbox.root())
    return _guarded(
        service.rename,
        target,
        str(args["symbol"]),
        str(args["new_name"]),
        int(args.get("near_line") or 0),
    )


def available(root: str | Path | None = None) -> bool:
    """Is any language server installed that could serve work under `root`?"""
    from kith.infra import workspace as sandbox

    try:
        return manager.any_available(root or sandbox.root())
    except Exception:
        return False
