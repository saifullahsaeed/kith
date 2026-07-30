"""His sandbox — a Linux machine that is genuinely his."""

from __future__ import annotations

from pathlib import Path

from kith.infra import sandbox
from kith.tools.params import INT, STR
from kith.tools.registry import tool


def _shell(command: str) -> dict:
    result = sandbox.run_command(command)
    return {"exitCode": result.exit_code, "output": result.output}


@tool(
    "shell",
    "Run a shell command on your own computer — a private Linux box where you are root. Returns combined stdout/stderr and the exit code. Do anything: install packages, build and run programs, manage files, poke around. For long-lived or background processes (servers, watchers, long builds), start them detached with `nohup … &` so they keep running after the command returns.",
    {"command": {**STR, "description": "The shell command to run (as root)."}},
    required=("command",),
)
def shell(path: Path, args: dict):
    return _shell(args["command"])


@tool(
    "read_file",
    "Read a file from your computer (relative paths are under /home/kith). Output "
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
