"""Working on the machine: files, search, and the shell."""

from __future__ import annotations

from pathlib import Path

from kith.infra import workspace as sandbox
from kith.tools.params import INT, STR
from kith.tools.registry import tool


def _shell(command: str) -> dict:
    result = sandbox.run_command(command)
    return {"exitCode": result.exit_code, "output": result.output}


@tool(
    "shell",
    "Run a shell command on your person's computer, from your own folder. Returns combined "
    "stdout/stderr and the exit code. Their shell, their PATH, their installed programs — so "
    "build and run things, use the tools already there, poke around. You are NOT root and "
    "this is not a private box: anything destructive, and anything outside your folder, needs "
    "their yes and will be refused until they give it. To delete something use delete_file, "
    "not `rm` — it goes to the Trash and `rm` cannot be undone by anyone. "
    "This WAITS for the command to finish, so only use it for things that finish: a server or "
    "a watcher goes to start_process, and a test suite to run_tests. Don't reach for "
    "`nohup … &` — it is refused here, because it would hand you a pid and nothing else. "
    "Nothing can answer a prompt either, so pass the flag that avoids the question "
    "(`-y`, `--yes`, `--no-input`) rather than hoping.",
    {"command": {**STR, "description": "The shell command to run."}},
    required=("command",),
)
def shell(path: Path, args: dict):
    return _shell(args["command"])


@tool(
    "read_file",
    "Read a file from your computer (relative paths are under your own folder). Output "
    "is line-numbered. A screenshot or image is shown to you as a picture instead, so "
    "you can judge what you actually made. For anything big, don't read it whole — pass "
    "`symbol` to get one function or class by name, or grep to find the line you want and "
    "read a window with `offset`/`limit`. A read without a "
    "range returns the first 400 lines and tells you if there's more.",
    {
        "path": STR,
        "symbol": {
            **STR,
            "description": (
                "Read just this definition — 'server_for', or 'Manager.server_for' when several "
                "classes have one by that name. Cheaper than reading the file and then finding "
                "it. Ignores offset/limit."
            ),
        },
        "offset": {**INT, "description": "1-based line to start at (optional)."},
        "limit": {**INT, "description": "How many lines to return (optional; default 400)."},
    },
    required=("path",),
)
def read_file(path: Path, args: dict):
    wanted = args["path"]
    # A screenshot asked for by name should be looked at, not decoded as text. He was taking
    # Playwright captures at 1440 and 390 all day and never seeing one of them, because this
    # function read bytes as UTF-8 and reported "not text". Routed rather than given a separate
    # tool name so "read the screenshot" simply works.
    if Path(str(wanted)).suffix.lower() in sandbox._IMAGE_SUFFIXES:
        from kith.config import model_capabilities

        if not model_capabilities().get("images"):
            return {
                "path": str(wanted),
                "note": "That is an image and this model cannot see images. Check it another "
                "way — its dimensions, or the DOM you rendered it from.",
            }
        return sandbox.read_image(str(wanted))
    symbol = str(args.get("symbol") or "").strip()
    if symbol:
        return _read_symbol(wanted, symbol)
    return sandbox.read_file(wanted, args.get("offset"), args.get("limit"))


def _read_symbol(wanted: str, symbol: str) -> str:
    """One definition, read through the same reader as everything else.

    `locate` answers *where*, and `read_file` does the reading — so the permission check, the
    numbering, the output budget and the "there is more, ask with offset=" sentence are the
    ones already in use rather than a second set of them here.

    The header exists because a window with no context is disorienting: a method arriving as
    lines 180-210 of a file whose length he does not know could be most of it or a rounding
    error, and that changes whether reading the rest is worth a round.
    """
    from kith.engine.code import excerpt, outline
    from kith.infra import permissions
    from kith.infra import workspace as sandbox

    target = Path(sandbox.resolve(wanted))
    permissions.require_path("read", target, sandbox.root())
    try:
        span = excerpt.locate(target, symbol)
    except (excerpt.ExcerptError, outline.OutlineError) as exc:
        # Returned rather than raised: every one of these messages names what to do instead —
        # the definitions that do exist, the ones that matched, or "grep it" — and that is
        # worth more to him than a failed tool call.
        return str(exc)
    body = sandbox.read_file(wanted, span.line, span.count)
    return f"{span.qualified} — {span.kind}, lines {span.line}-{span.end_line} of {span.of_lines}\n{body}"


@tool(
    "write_file",
    "Write (or overwrite) a file on your computer, creating parent folders as needed. For a "
    "file that already exists, use edit_file instead: rewriting a whole file to alter one "
    "line costs you the file again in output, silently loses anything you did not retype, "
    "and flattens its formatting a little more each time.",
    {"path": STR, "content": STR},
    required=("path", "content"),
)
def write_file(path: Path, args: dict):
    return sandbox.write_file(args["path"], args.get("content") or "")


@tool(
    "edit_file",
    "Change part of a file by replacing an exact piece of text. Use this instead of "
    "write_file for any change to a file that already exists — write_file replaces the whole "
    "thing, which costs you the entire file in output and loses anything you did not retype. "
    "`old` must appear EXACTLY once, whitespace and indentation included: copy it verbatim "
    "from a read. If it appears more than once you will be told how many times, and you "
    "should either include more surrounding lines to pin down the one you mean or pass "
    "replace_all. You get back a diff of what changed — read it, that is how you check you "
    "changed what you intended.",
    {
        "path": STR,
        "old": {**STR, "description": "The exact text to replace, copied verbatim."},
        "new": {**STR, "description": "What to put in its place."},
        "replace_all": {
            "type": "boolean",
            "description": "Replace every occurrence instead of failing on ambiguity.",
        },
    },
    required=("path", "old", "new"),
)
def edit_file(path: Path, args: dict):
    return sandbox.edit_file(
        args["path"],
        args.get("old") or "",
        args.get("new") or "",
        replace_all=bool(args.get("replace_all")),
    )


@tool(
    "edit_files",
    "Make several edits at once, as one all-or-nothing change. Use this the moment a change "
    "touches more than one place — renaming something used in eight files, updating every "
    "call site, applying the same fix across a folder. One edit per call costs you a whole "
    "round each time, and you only get so many before a job has to stop; this costs one. "
    "Each edit is {path, old, new} with the same rules as edit_file: `old` copied verbatim, "
    "unique in its file unless you pass replace_all. Either every edit applies or none does, "
    "so a batch that fails leaves the files untouched and tells you which edit was wrong. "
    "Edits to the same file are applied in the order you give them, so a later one can build "
    "on an earlier one. You get back one combined diff — read it.",
    {
        "edits": {
            "type": "array",
            "description": "The edits to apply, in order.",
            "items": {
                "type": "object",
                "properties": {
                    "path": STR,
                    "old": {**STR, "description": "The exact text to replace, copied verbatim."},
                    "new": {**STR, "description": "What to put in its place."},
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace every occurrence in that file instead of failing on ambiguity.",
                    },
                },
                "required": ["path", "old", "new"],
            },
        }
    },
    required=("edits",),
)
def edit_files(path: Path, args: dict):
    raw = args.get("edits")
    if not isinstance(raw, list):
        return {"error": "edits must be a list of {path, old, new}"}
    return sandbox.edit_files([one for one in raw if isinstance(one, dict)])


@tool(
    "delete_file",
    "Put a file or folder in the Trash. Use this rather than `rm` in the shell — it goes "
    "to the Trash, so your person can get it back if you were wrong about which one they "
    "meant, and `rm` cannot be undone by anyone.",
    {"path": STR},
    required=("path",),
)
def delete_file(path: Path, args: dict):
    # This tool exists because it did not, and its absence had a cost: asked to delete a
    # file from the Desktop, the only route available was `shell` with `rm`, which is both
    # the least supervised path in the system and the one that destroys rather than
    # recovers. A first-class action means the permission check applies and the Trash does
    # the rest.
    return {"trashed": args["path"], "where": sandbox.remove(args["path"])}


@tool(
    "check_code",
    "Run whatever this project is checked with — TypeScript, ruff, or its build — and get back "
    "only what is wrong. Do this before you say something is done. It works out what to run "
    "from what is in the folder, so you do not have to know. If you changed something "
    'visual, look at a screenshot of it too — you can see images, and "the build passed" is not the same as "it looks right".',
    {"path": {**STR, "description": "The project folder (default: your whole folder)."}},
    required=(),
)
def check_code(path: Path, args: dict):
    return sandbox.check_code(args.get("path") or ".")


@tool(
    "glob",
    "Find files by name pattern, newest first — 'where are the tests', 'which components exist'. "
    "Use `**/*.tsx` style patterns. grep searches inside files; this searches their names.",
    {
        "pattern": {**STR, "description": "A glob like '**/*.py' or 'test_*.py'."},
        "path": {**STR, "description": "Folder to search under (default: your whole folder)."},
    },
    required=("pattern",),
)
def glob(path: Path, args: dict):
    return sandbox.glob(args["pattern"], args.get("path") or ".")


@tool(
    "changes",
    "See what you have changed and not yet committed, as a diff. Use it before you claim "
    "something is done: it is the only way to check that what you changed is what you meant "
    "to change, and it catches the edit you made and forgot. Pass a path to narrow it to one "
    "file or folder. This shows everything since your last `commit`, so if it is longer than "
    "you expected you have work you have not recorded yet.",
    {"path": {**STR, "description": "Optional file or folder to limit the diff to."}},
    required=(),
)
def changes(path: Path, args: dict):
    return sandbox.diff(args.get("path") or None)


@tool(
    "commit",
    "Save a point in your folder's history, with a message saying what you did. Do this when "
    "something works — a feature finished, a bug fixed, a checker passing — not on every step "
    "and not mid-change. A commit is a claim that this is a coherent point worth coming back "
    "to, so make it when that is true: it is how you can undo a wrong turn, and how your "
    "person can review what you did while they were away. Check `changes` first if you are not "
    "sure what you are about to record.",
    {
        "message": {
            **STR,
            "description": "What this change does, in one line. Written for someone reading "
            "the history later, not for you now.",
        }
    },
    required=("message",),
)
def commit(path: Path, args: dict):
    summary = sandbox.commit_all(args.get("message") or "")
    if not summary:
        return {"committed": False, "note": "Nothing had changed, so there was nothing to record."}
    return {"committed": True, "changed": summary}


@tool(
    "history",
    "The recent history of your folder — what changed, and when. Useful for picking up where "
    "you left off, or checking whether you already did something.",
    {"limit": {**INT, "description": "How many entries (default 20)."}},
    required=(),
)
def history(path: Path, args: dict):
    return sandbox.log(int(args.get("limit") or 20))


@tool(
    "list_files",
    "List a directory on your computer (defaults to your home).",
    {"path": STR},
    required=(),
)
def list_files(path: Path, args: dict):
    return sandbox.list_files(args.get("path") or ".")


@tool(
    "grep",
    "Search files for a pattern (ripgrep) and get back matching lines with "
    "file:line numbers — your way to find the needle without loading whole "
    "haystacks into your head. Then read_file just that slice. Supports a glob "
    "filter like '*.py'.",
    {
        "pattern": {**STR, "description": "Regex or literal to search for."},
        "path": {**STR, "description": "File or directory to search (default: home)."},
        "glob": {**STR, "description": "Optional filename filter, e.g. '*.md'."},
    },
    required=("pattern",),
)
def grep(path: Path, args: dict):
    return sandbox.grep(args["pattern"], args.get("path") or ".", args.get("glob"))
