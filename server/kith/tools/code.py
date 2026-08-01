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
from kith.services.code import processes as process_service
from kith.services.code import repomap as repomap_service
from kith.services.code import testing as testing_service
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


@tool(
    "run_tests",
    "Run this project's tests and get back what failed. Do this before you say a change "
    "works: a build passing and a typechecker passing both mean the code is well-formed, "
    "which is not the same thing and is exactly the evidence that looks strongest while "
    "proving least. It works out the runner from the project itself, and reports counts plus "
    "the failing test names rather than the whole log. Pass `filter` with a file path or a "
    "test name to run just that one while you are fixing it — much faster, and the noise of "
    "the other two hundred is not what you need.",
    {
        "path": {**STR, "description": "The project folder (default: your whole folder)."},
        "filter": {
            **STR,
            "description": "A file path or test name to run only that (optional).",
        },
    },
    required=(),
)
def run_tests(path: Path, args: dict):
    from kith.infra import workspace as sandbox
    from kith.services import permissions

    target = Path(sandbox.resolve(args.get("path") or "."))
    permissions.require_path("read", target, sandbox.root())
    try:
        return testing_service.run(str(args.get("path") or "."), str(args.get("filter") or ""))
    except testing_service.TestingError as exc:
        return {"error": str(exc)}


@tool(
    "start_process",
    "Start something long-running and keep a handle on it — a dev server, a watcher, a build "
    "that takes minutes. Unlike `shell`, this returns straight away instead of waiting, and "
    "unlike `nohup … &` you can still see it: use check_process to read what it has printed "
    "since you last looked, and stop_process when you are done. Give it a short name you will "
    "recognise ('dev-server', 'tests-watch'). Always stop what you started before you finish.",
    {
        "command": {**STR, "description": "The command to run, e.g. 'npm run dev'."},
        "name": {**STR, "description": "A short name to refer to it by, e.g. 'dev-server'."},
    },
    required=("command", "name"),
)
def start_process(path: Path, args: dict):
    try:
        return process_service.processes.start(str(args["command"]), str(args["name"]))
    except process_service.ProcessError as exc:
        return {"error": str(exc)}


@tool(
    "check_process",
    "See what a background process has printed since you last looked, and whether it is still "
    "alive. Only the new output, so you can check a watcher repeatedly without re-reading its "
    "startup banner every time. Call it with no name to list everything you have running. If "
    "something exited, this is how you find out and what it said on the way out.",
    {"name": {**STR, "description": "Which one (optional — omit to list them all)."}},
    required=(),
)
def check_process(path: Path, args: dict):
    try:
        return process_service.processes.check(str(args.get("name") or ""))
    except process_service.ProcessError as exc:
        return {"error": str(exc)}


@tool(
    "stop_process",
    "Stop a background process you started, and everything it started in turn. Do this when "
    "you are finished with it — a dev server left running holds its port, and the next thing "
    "that needs that port fails with an error that looks nothing like the real cause.",
    {"name": {**STR, "description": "Which one to stop."}},
    required=("name",),
)
def stop_process(path: Path, args: dict):
    try:
        return process_service.processes.stop(str(args["name"]))
    except process_service.ProcessError as exc:
        return {"error": str(exc)}
