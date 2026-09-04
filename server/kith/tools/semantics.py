"""Tools that need a language server, and are honest when there isn't one.

Every one of these can fail for a reason that is nobody's mistake — nothing installed, a
server that will not start, a symbol that is not in the file. All of those come back as a
readable result rather than an exception, because an exception ends the round and a sentence
lets him try the next thing. `repo_map` and `grep` are always the next thing, and the messages
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
#: `rename_symbol` alone now. `diagnostics`, `references` and `definition` were all merged
#: into tools that work without a server and reach for one when it is there — `check_code` and
#: `find_symbol` — so hiding them is no longer the question; those two are always offered and
#: say which engine answered. A rename is different: there is no honest parser-only version of
#: "change this everywhere it is used and nowhere it merely appears", so the tool that promises
#: it must disappear when nothing can keep the promise.
NEEDS_A_LANGUAGE_SERVER = frozenset({"rename_symbol"})

#: The mirror image, and it has to be one or the pair is incoherent. `install_language_support`
#: is worth its schema exactly when `rename_symbol` is hidden — offering "install a language
#: server" on a machine that already has one is a line of prompt paid on every round to suggest
#: something with no effect, and offering it *only* when servers exist would be a tool that can
#: never fix the thing it is for.
OFFERED_WITHOUT_A_LANGUAGE_SERVER = frozenset({"install_language_support"})

_SYMBOL = {**STR, "description": "The name to ask about, exactly as it is written in the code."}
_NEAR = {
    **INT,
    "description": "Line to disambiguate by, if the name appears more than once (optional).",
}


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


def diagnostics(target: Path):
    """What the language server says is wrong with one file. Reached through `check_code`."""
    return _guarded(service.diagnostics, target)


def definition_and_references(target: Path, symbol: str, near_line: int = 0) -> dict:
    """Where a name is defined and everywhere it is used, from the language server.

    Both, in one answer, because that is the shape `find_symbol` already had and the shape
    that made it the tool worth reaching for. Asking them separately was two schemas and a
    choice — and the choice was usually wrong in the same direction: `definition` alone, then
    a second round for the callers, on the way to a change that needed both.

    `_guarded` is applied per call rather than around the pair, so a server that answers one
    and fails the other reports the half it has instead of nothing.

    **Flattened to the same keys the parser path uses**, deliberately. Nesting the two results
    would mean `definitions` holding a list from one engine and a dict from the other under
    one tool name, which is the kind of shape that reads fine to whoever wrote it and is
    unusable to anything consuming the tool.
    """
    where = _guarded(service.definition, target, symbol, near_line)
    uses = _guarded(service.references, target, symbol, near_line)
    out: dict = {"definitions": [], "references": []}
    for half in (where, uses):
        if not isinstance(half, dict):
            continue
        # `unavailable` is the useful one to surface: it names what to try instead — grep,
        # or installing a server — where `error` only says the server broke.
        if half.get("unavailable"):
            out["unavailable"] = half["unavailable"]
        elif half.get("error"):
            out.setdefault("error", half["error"])
    if isinstance(where, dict):
        out["definitions"] = where.get("definitions") or []
    if isinstance(uses, dict):
        out["references"] = uses.get("references") or []
    return out


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
    "(`find_symbol`, `rename_symbol`, `check_code`) give their exact answers here. Installs only "
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
