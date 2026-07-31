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
    "Run a shell command on your person's computer, from your own folder. Returns combined stdout/stderr and the exit code. Their shell, their PATH, their installed programs — so build and run things, use the tools already there, poke around. You are NOT root and this is not a private box: anything destructive, and anything outside your folder, needs their yes and will be refused until they give it. To delete something use delete_file, not `rm` — it goes to the Trash and `rm` cannot be undone by anyone. For long-lived processes (servers, watchers, long builds), start them detached with `nohup … &`.",
    {"command": {**STR, "description": "The shell command to run."}},
    required=("command",),
)
def shell(path: Path, args: dict):
    return _shell(args["command"])


@tool(
    "read_file",
    "Read a file from your computer (relative paths are under your own folder). Output "
    "is line-numbered. A screenshot or image is shown to you as a picture instead, so "
    "you can judge what you actually made. For anything big, don't read it whole — grep to find the "
    "line you want, then read a window with `offset`/`limit`. A read without a "
    "range returns the first 400 lines and tells you if there's more.",
    {
        "path": STR,
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
    return sandbox.read_file(wanted, args.get("offset"), args.get("limit"))


@tool(
    "write_file",
    "Write (or overwrite) a file on your computer, creating parent folders as needed.",
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
    "from what is in the folder, so you do not have to know.",
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
