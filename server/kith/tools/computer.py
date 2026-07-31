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
    "is line-numbered. For anything big, don't read it whole — grep to find the "
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
    return sandbox.read_file(args["path"], args.get("offset"), args.get("limit"))


@tool(
    "write_file",
    "Write (or overwrite) a file on your computer, creating parent folders as needed.",
    {"path": STR, "content": STR},
    required=("path", "content"),
)
def write_file(path: Path, args: dict):
    return sandbox.write_file(args["path"], args.get("content") or "")


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
