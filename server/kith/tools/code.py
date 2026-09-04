"""Reading code as structure: the shape of a file, and the shape of a codebase.

These are the cheap half of understanding code. They parse, so they need no language server,
no project setup and no network, and they answer the two questions that otherwise cost him a
dozen rounds of grep-and-open: *what is in this file* and *what is in this repository*.

The semantic questions — who calls this, what breaks if I rename it — live in `semantics.py`
next door and need a language server, which is allowed to be missing.
"""

from __future__ import annotations

from pathlib import Path

from kith.domain.tooling import many
from kith.engine.code import outline as outline_service
from kith.engine.code import repomap as repomap_service
from kith.engine.code import search as search_service
from kith.engine.run import processes as process_service
from kith.engine.run import testing as testing_service
from kith.tools.params import INT, LIST_STR, STR
from kith.tools.registry import tool


def _outlines(args: dict):
    """The shape of named files. Reached through `repo_map(paths=…)`, which owns the schema."""
    wanted = many(args, "paths", "path")
    if not wanted:
        return {"error": "Nothing to outline — `paths` is a list of source files."}
    shapes = [_outline_one(one) for one in wanted]
    if len(shapes) == 1:
        return shapes[0]
    # Combined into the shape a single outline already has, rather than a second one. Every
    # reader of this result — the model, the transcript, the panel that renders a listing of
    # definitions — knows that shape, and `path` is a label rather than a lookup key.
    return {
        "path": f"{len(shapes)} files",
        "language": ", ".join(sorted({str(one.get("language") or "?") for one in shapes})),
        "definitions": sum(int(one.get("definitions") or 0) for one in shapes),
        "outline": "\n\n".join(
            f"===== {one.get('path')} =====\n{one.get('outline') or one.get('error') or ''}" for one in shapes
        ),
    }


def _outline_one(wanted: str) -> dict:
    from kith.infra import permissions
    from kith.infra import workspace as sandbox

    target = Path(sandbox.resolve(wanted))
    permissions.require_path("read", target, sandbox.root())
    try:
        found = outline_service.of_file(target)
    except outline_service.OutlineError as exc:
        # Named with its path, because in a batch "not a language I can parse" with no file
        # attached is a sentence you cannot act on.
        return {"path": str(wanted), "error": str(exc)}
    return {
        "path": str(wanted),
        "language": found["language"],
        "definitions": len(found["symbols"]),
        "outline": outline_service.render(found),
    }


@tool(
    "repo_map",
    "The shape of code, without reading it. With no arguments: what a whole codebase contains, "
    "its files and the definitions in each — the orientation you would want on your first day, "
    "for about the cost of opening two files, so use it when you land in a project you do not "
    "know instead of listing directories and guessing at greps. Pass `focus` with a word from "
    "what you are after ('auth', 'invoice') to rank the relevant files first, and believe the "
    "count of what it left out rather than assuming you got everything. "
    "Pass `paths` instead for the shape of particular files — every class, function and method "
    "with the line it starts on. Do that BEFORE read_file on anything you do not know: a "
    "2,000-line module costs you the whole module for the rest of the turn, and its shape "
    "costs a paragraph. Then read_file the part you want with offset and limit. "
    "Needs nothing installed either way. "
    "Mapping a codebase you do not know, or outlining a whole subsystem to get your bearings, "
    "is an errand: delegate_subtask gets its bearings and tells you the shape, and none of it "
    "lands in your window.",
    {
        "path": {**STR, "description": "The project folder (default: your whole folder)."},
        "paths": {
            **LIST_STR,
            "description": "Particular source files to outline instead of mapping a folder. "
            "A LIST — ask for the whole set you care about in one call.",
        },
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
    """A folder's map and a file's outline are the same question at two scales.

    They were two tools, `repo_map` and `outline`, and the model had to decide which scale it
    wanted before it had seen anything — which is the decision it is least equipped to make on
    arrival. Worse, the two answers were nearly the same shape, so a turn that picked wrong
    spent a round finding out and a second one asking again.

    The engines stay separate underneath: `repomap` ranks and budgets across a tree,
    `outline` parses named files exactly. `paths` is the switch, and it is a switch rather
    than a merge because "these four files" and "everything under here, ranked, to a token
    budget" genuinely are different work.
    """
    from kith.infra import permissions
    from kith.infra import workspace as sandbox

    if args.get("paths"):
        return _outlines(args)

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
    "Where a name is defined and where it is used — by what the code means, not by matching "
    "characters. Use this instead of grep for any function, class or method name: grep also "
    "returns the word in comments, in strings, inside longer names, and in every unrelated "
    "local variable that happens to share it, so 'no matches' from grep is not evidence that "
    "nothing calls something. Use it before you change or delete anything shared. "
    "Give `path` as the FILE the name appears in when you know it — that gets the language "
    "server's answer, which follows imports, re-exports and aliases exactly — or as a folder "
    "to search when you do not. Either way it says which engine answered; the parser works "
    "in 19 languages and needs nothing installed.",
    {
        "name": {**STR, "description": "The exact function, class or method name."},
        "path": {
            **STR,
            "description": "The file the name appears in, or a folder to search "
            "(default: your whole folder).",
        },
        "near_line": {
            **INT,
            "description": "With a file: the line to disambiguate by, if the name appears "
            "more than once in it (optional).",
        },
    },
    required=("name",),
)
def find_symbol(path: Path, args: dict):
    """One question, two engines, and the caller does not have to know which it is getting.

    `definition` and `references` were separate tools that answered this exactly, from a
    language server, and were hidden entirely when none was installed. So the model saw either
    three tools for two questions or one tool that quietly gave the weaker answer, and choosing
    between them meant knowing whether a server was running for this project's language —
    which is not something it can see.

    Now the file/folder shape of `path` says what is possible and the answer says what
    happened. A file with a server behind it gets the exact answer; anything else gets the
    parser, which is the honest fallback rather than a silent downgrade, because `engine` is
    in the result either way.
    """
    from kith.infra import permissions
    from kith.infra import workspace as sandbox
    from kith.tools import semantics

    wanted = str(args.get("name") or "")
    asked = Path(sandbox.resolve(args.get("path") or "."))
    unavailable = ""
    if asked.is_file() and semantics.available(asked.parent):
        permissions.require_path("read", asked, sandbox.root())
        found = semantics.definition_and_references(asked, wanted, int(args.get("near_line") or 0))
        # An `unavailable` from the server is not an answer, and returned verbatim it reads as
        # one — empty `definitions` and `references` beside a sentence the caller may not act
        # on. It is reachable by naming a method inherited from a base class, which is an
        # ordinary thing to ask about. So the parser gets a go.
        #
        # The sentence is kept, though, and put back below if the parser finds nothing either:
        # "that name is not in this file, try …" is a *useful* nothing, and dropping it in
        # favour of a bare empty result would trade one bad answer for another.
        if not found.get("unavailable"):
            return {"name": wanted, "engine": "language server", **found}
        unavailable = str(found["unavailable"])

    # A file is where the name *is*, not where its callers are, so the parser searches the
    # project the file belongs to rather than the one folder it sits in.
    #
    # This searched `asked.parent`, which turned "who uses this" into "who uses this in the same
    # directory" — silently, under a plain `engine: "parser"`. Measured: a symbol defined in
    # `pkg/target.py` and called from `other/caller.py` came back `references: []`,
    # `filesSearched: 1`. A false "nothing uses this" from the one tool whose own description
    # warns that "'no matches' from grep is not evidence that nothing calls something" is the
    # conclusion that precedes deleting shared code.
    target = _project_root(asked) if asked.is_file() else asked
    permissions.require_path("read", target, sandbox.root())
    try:
        found = search_service.find(target, wanted)
    except search_service.SearchError as exc:
        return {"error": str(exc)}
    # The same keys the language-server path returns, so a caller reads one shape whichever
    # engine answered. `calls` was this tool's word for the same thing the server calls
    # references; one tool cannot have two words for it.
    # The hit lists, and not a rendering of them as well.
    #
    # This returned both — `definitions`/`references` *and* `search_service.render()` of the
    # same data, measured at 3.6x redundant — on a change whose entire purpose was sending
    # fewer tokens. The lists are the shape the language-server branch returns, so they are the
    # shape that stays: one answer, readable by whatever consumes it, and the same keys
    # whichever engine answered.
    answer = {
        "name": found["name"],
        "engine": "parser",
        # What was actually searched, because the honest answer to "nothing uses this" depends
        # on it and the caller cannot see the scope from here.
        "searched": str(target),
        "definitions": found["definitions"],
        "references": found["calls"],
        "filesSearched": found["files_searched"],
    }
    if unavailable and not found["definitions"] and not found["calls"]:
        answer["unavailable"] = unavailable
    return answer


#: What marks the top of a project, for deciding how wide "across a project" is.
_ROOTS = ("pyproject.toml", "package.json", "go.mod", "Cargo.toml", ".git", "tsconfig.json")


def _project_root(file: Path) -> Path:
    """The project a file belongs to, or its own folder if nothing above it says.

    Bounded by the workspace root, so this cannot walk out into somebody's home directory
    looking for a `package.json`.
    """
    from kith.infra import workspace as sandbox

    here = Path(sandbox.root())
    probe = file.parent
    while True:
        if any((probe / marker).exists() for marker in _ROOTS):
            return probe
        if probe == here or probe.parent == probe or here not in probe.parents:
            return file.parent
        probe = probe.parent


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
