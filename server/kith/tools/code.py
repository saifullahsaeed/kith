"""Reading code as structure: the shape of a file, and the shape of a codebase.

These are the cheap half of understanding code. They parse, so they need no language server,
no project setup and no network, and they answer the two questions that otherwise cost him a
dozen rounds of grep-and-open: *what is in this file* and *what is in this repository*.

The semantic questions — who calls this, what breaks if I rename it — live in `semantics.py`
next door and need a language server, which is allowed to be missing.
"""

from __future__ import annotations

from pathlib import Path

from kith.services.code import outline as outline_service
from kith.services.code import repomap as repomap_service
from kith.tools.params import INT, STR
from kith.tools.registry import tool


@tool(
    "outline",
    "The shape of a source file — every class, function and method in it, with the line each "
    "one starts on — without reading the file. Do this BEFORE read_file on anything you do "
    "not already know: a 2,000-line module costs you the whole module for the rest of the "
    "turn, and its outline costs about a paragraph. Read the outline, find the thing you "
    "want, then read_file that part with offset and limit. Works on most languages and needs "
    "nothing installed.",
    {"path": {**STR, "description": "The source file to outline."}},
    required=("path",),
)
def outline(path: Path, args: dict):
    from kith.infra import workspace as sandbox
    from kith.services import permissions

    target = Path(sandbox.resolve(args["path"]))
    permissions.require_path("read", target, sandbox.root())
    try:
        found = outline_service.of_file(target)
    except outline_service.OutlineError as exc:
        return {"error": str(exc)}
    return {
        "path": str(args["path"]),
        "language": found["language"],
        "definitions": len(found["symbols"]),
        "outline": outline_service.render(found),
    }


@tool(
    "repo_map",
    "What a codebase contains — its files and the definitions in each — in one read. Use this "
    "when you land in a project you do not know, instead of listing directories and guessing "
    "at greps: it is the orientation you would want on your first day, and it costs about as "
    "much as opening two files. Pass `focus` with a word from what you are looking for "
    "('auth', 'invoice') to rank the relevant files first — on a big repository that matters "
    "more than the budget does. It tells you how many files it left out; believe that number "
    "rather than assuming what you got is everything.",
    {
        "path": {**STR, "description": "The project folder (default: your whole folder)."},
        "focus": {
            **STR,
            "description": "Optional word to rank files by — matched against the file path.",
        },
        "budget_tokens": {
            **INT,
            "description": "Roughly how much context to spend (default 3000).",
        },
    },
    required=(),
)
def repo_map(path: Path, args: dict):
    from kith.infra import workspace as sandbox
    from kith.services import permissions

    target = Path(sandbox.resolve(args.get("path") or "."))
    permissions.require_path("read", target, sandbox.root())

    raw = args.get("budget_tokens")
    try:
        budget = int(raw) if raw else repomap_service.DEFAULT_TOKENS
    except (TypeError, ValueError):
        budget = repomap_service.DEFAULT_TOKENS
    # A map is orientation, not a dump.
    budget = max(300, min(budget, repomap_service.MAX_BUDGET_TOKENS))

    try:
        mapped = repomap_service.build(target, budget_tokens=budget, focus=str(args.get("focus") or ""))
    except repomap_service.RepoMapError as exc:
        return {"error": str(exc)}
    return {
        "root": mapped["root"],
        "filesShown": mapped["files_shown"],
        "filesFound": mapped["files_found"],
        "map": repomap_service.render(mapped),
    }
