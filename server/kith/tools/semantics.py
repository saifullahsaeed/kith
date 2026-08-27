"""Tools that need a language server, and are honest when there isn't one.

Every one of these can fail for a reason that is nobody's mistake — nothing installed, a
server that will not start, a symbol that is not in the file. All of those come back as a
readable result rather than an exception, because an exception ends the round and a sentence
lets him try the next thing. `outline` and `grep` are always the next thing, and the messages
say so.
"""

from __future__ import annotations

from pathlib import Path

from kith.engine.code.lsp import semantics as service
from kith.engine.code.lsp.manager import Unavailable, manager
from kith.tools.params import INT, STR
from kith.tools.registry import tool

#: The tools in this module. `tool_schemas` consults this to leave them out of the prompt
#: entirely when no language server can serve the folder being worked in — a schema costs
#: tokens on every round, and advertising a capability that will always answer "not
#: installed" is exactly the waste the toolset lists exist to prevent.
NEEDS_A_LANGUAGE_SERVER = frozenset({"diagnostics", "references", "definition", "rename_symbol"})

#: The mirror image, and it has to be one or the pair is incoherent. `install_language_support`
#: is worth its schema exactly when the four above are hidden — offering "install a language
#: server" on a machine that already has one is a line of prompt paid on every round to suggest
#: something with no effect, and offering it *only* when servers exist would be a tool that can
#: never fix the thing it is for.
OFFERED_WITHOUT_A_LANGUAGE_SERVER = frozenset({"install_language_support"})

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
    from kith.engine.code.lsp.client import LSPError

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
    "Where a name comes from — the file and line it is defined on. Needs a language server; if none is installed use `find_symbol`, which works anywhere. Faster and surer than "
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


@tool(
    "install_language_support",
    "Get the language server for what this project is written in, so the semantic tools "
    "(references, definition, rename_symbol, diagnostics) start working here. Installs only "
    "what this project actually needs — never every language — into Kith's own folder, never "
    "globally. It asks first. Offer this when you find yourself grepping for callers because "
    "the semantic tools are not available; do not run it speculatively.",
    {
        "path": {**STR, "description": "The project folder (default: where you are working)."},
        "confirm": {
            **STR,
            "description": (
                "Leave empty to see what would be installed and how big it is. Pass the "
                "language name from that answer to actually install it."
            ),
        },
    },
    required=(),
)
def install_language_support(path: Path, args: dict):
    """Report what is missing, or install one family of it.

    Two steps on purpose. Called without `confirm` it only *looks* — which languages this
    project is written in, which of those have no server, and which of those we can install.
    That answer is what he shows the person. Called with a family name it installs that one,
    through the ordinary command gate, so what gets approved is the literal command that runs.
    """
    from kith.engine.code.lsp import install
    from kith.infra import permissions
    from kith.infra import workspace as sandbox

    target = Path(sandbox.resolve(args.get("path") or "."))
    permissions.require_path("read", target, sandbox.root())

    def served(family: str) -> bool:
        try:
            return manager.find_binary(family, target) is not None
        except Exception:
            return False

    wanted = install.wanted_for(target, served)
    confirm = str(args.get("confirm") or "").strip().lower()

    if not confirm:
        if not wanted:
            return {
                "needed": [],
                "note": "Nothing to install — either every language here is already served, or "
                "it is one I do not install (Go, Rust, Ruby and C come from their own package "
                "managers; ask and I will tell you the command).",
            }
        return {
            "needed": wanted,
            "note": "Ask them before installing. "
            + "; ".join(f"{family}: `{install.command_for(family)}`" for family in wanted)
            + ". Then call this again with confirm set to the language.",
        }

    if confirm not in wanted:
        return {
            "error": f"{confirm} is not something to install here. "
            + (f"This project wants: {', '.join(wanted)}." if wanted else "Nothing is missing.")
        }

    command = install.command_for(confirm)
    if command is None:
        return {"error": f"There is no installer for {confirm}."}
    # The gate sees the exact string that will run, so the thing approved and the thing
    # executed cannot drift apart — and it is told what the command is *for*, because
    # otherwise the dialog is a path under ~/.kith and a sentence about writing outside the
    # workspace, which is accurate and impossible to make a decision from.
    permissions.require_command(
        command,
        sandbox.root(),
        purpose=(
            f"he wants to install the {confirm} language server ({install.download_size(confirm)}) "
            f"from npm, into {install.prefix()} — delete that folder to undo it"
        ),
    )
    worked, said = install.run(confirm)
    if not worked:
        return {"error": said}
    # Discovery caches nothing about absence except a short "this one would not start" note,
    # so the newly installed server is found on the next call without a restart.
    return {"installed": confirm, "where": said}
