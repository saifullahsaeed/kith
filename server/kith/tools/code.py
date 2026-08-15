"""Reading code as structure: the shape of a file, and the shape of a codebase.

These are the cheap half of understanding code. They parse, so they need no language server,
no project setup and no network, and they answer the two questions that otherwise cost him a
dozen rounds of grep-and-open: *what is in this file* and *what is in this repository*.

The semantic questions — who calls this, what breaks if I rename it — live in `semantics.py`
next door and need a language server, which is allowed to be missing.
"""

from __future__ import annotations

from pathlib import Path

from kith.engine.code import outline as outline_service
from kith.engine.code import repomap as repomap_service
from kith.engine.code import search as search_service
from kith.engine.run import processes as process_service
from kith.engine.run import testing as testing_service
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
    from kith.infra import permissions
    from kith.infra import workspace as sandbox

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
        "changed_since": {
            **STR,
            "description": (
                "A git ref — 'main', 'HEAD~3'. Narrows the map to what differs from it, "
                "including files not yet committed. Use this to review a change instead of "
                "mapping the whole repository."
            ),
        },
    },
    required=(),
)
def repo_map(path: Path, args: dict):
    from kith.infra import permissions
    from kith.infra import workspace as sandbox

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
        mapped = repomap_service.build(
            target,
            budget_tokens=budget,
            focus=str(args.get("focus") or ""),
            changed_since=str(args.get("changed_since") or ""),
        )
    except repomap_service.RepoMapError as exc:
        return {"error": str(exc)}
    return {
        "root": mapped["root"],
        "filesShown": mapped["files_shown"],
        "filesFound": mapped["files_found"],
        "map": repomap_service.render(mapped),
    }


@tool(
    "find_symbol",
    "Where a name is defined and where it is called, across a project — by what the code "
    "means, not by matching characters. Use this instead of grep for any function, class or "
    "method name: grep also returns the word in comments, in strings, inside longer names, and "
    "in every unrelated local variable that happens to share it. This returns definitions and "
    "call sites, separately, and tells you how many files it read. Needs nothing installed and "
    "works in 19 languages.",
    {
        "name": {**STR, "description": "The exact function, class or method name."},
        "path": {**STR, "description": "The folder to search (default: your whole folder)."},
    },
    required=("name",),
)
def find_symbol(path: Path, args: dict):
    from kith.infra import permissions
    from kith.infra import workspace as sandbox

    target = Path(sandbox.resolve(args.get("path") or "."))
    permissions.require_path("read", target, sandbox.root())
    try:
        found = search_service.find(target, str(args.get("name") or ""))
    except search_service.SearchError as exc:
        return {"error": str(exc)}
    return {
        "name": found["name"],
        "definitions": len(found["definitions"]),
        "calls": len(found["calls"]),
        "filesSearched": found["files_searched"],
        "found": search_service.render(found),
    }


@tool(
    "run_tests",
    "Run this project's tests and get back what failed. Do this before you say a change "
    "works: a build passing and a typechecker passing both mean the code is well-formed, "
    "which is not the same thing and is exactly the evidence that looks strongest while "
    "proving least. It works out the runner from the project itself, and reports counts plus "
    "the failing test names rather than the whole log. Pass `filter` with a file path or a "
    "test name to run just that one while you are fixing it — much faster, and the noise of "
    "the other two hundred is not what you need. A slow suite does not block you: if it is "
    'not done after a wait you get back `status: "running"` instead of the usual counts. '
    "When that happens, say so and finish the turn. **Do not set a reminder to check back, and "
    "do not keep calling this to see if it is done yet** — when the run finishes it comes back "
    "to you on its own, in this conversation, with the exit code and the output. Checking costs "
    "a whole round; waiting costs nothing.",
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
    from kith.infra import permissions
    from kith.infra import workspace as sandbox

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
    "recognise ('dev-server', 'tests-watch'). "
    "**A task that finishes comes back to you here, on its own** — the exit code and its last "
    "output arrive in this conversation whenever it ends, however long that takes. So start it, "
    "say what you started, and get on with something else or finish the turn. Do not set a "
    "reminder to check on it and do not poll it: both spend a whole round asking a question that "
    "answers itself. A long-lived thing you never expect to end — a dev server, a watcher — is "
    "the one case to stop yourself, and to stop before you finish.",
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
    "startup banner every time. Call it with no name to list everything you have running. "
    "For a *watcher* — something with no end, printing as it goes — this is how you read it. "
    "For a task you are waiting to *finish*, you do not need this at all: the ending comes back "
    "to you by itself. Use it when you want the output now, not to find out whether it is done.",
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
