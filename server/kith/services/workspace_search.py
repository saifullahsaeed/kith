"""Finding a file in his folder by what it is called or by what it says.

The ⌘K palette could only match names, and only in the folders something had already listed —
the root, and whatever the Files page had opened. So "where did he write the retry policy" had no
answer unless you already knew which folder to open first, which is the question being asked.

This answers it on the server, where the whole tree is one walk away rather than forty requests on
a connection pool of six. Three choices worth knowing:

**Substring, case-insensitive, and the whole query as one string.** The palette ranks and bolds on
its side; what it needs from here is every place the text occurs, not a second opinion on what the
person meant. A regex would make `a.b` mean something other than what was typed.

**Bounded by count and by clock, and says when it stopped.** It runs on every pause in typing, so
a search that walks a monorepo's worth of files for ten seconds is not a slow search, it is a
worker taken out of the pool while the next keystroke's request queues behind it. `truncated` is
how the interface can say "showing the first 50" rather than implying that was all of them.

**ripgrep when it is on this machine, Python when it is not.** Found through `executables.which`
rather than `shutil.which`, because a Finder-launched app inherits launchd's four-entry `PATH`
where ripgrep never is (see that module). Both paths skip the same folders, skip binaries and files
over a megabyte, and never follow a link out of his folder — the fallback is not a degraded mode,
it is the same search done slower.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from kith.infra import executables
from kith.infra import workspace as sandbox

#: Folders nobody is looking inside: dependencies, caches, build output. The same list the agent's
#: own `grep` excludes, so the palette and he agree on what "his files" means.
SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        "out",
        "release",
        "coverage",
        ".next",
        ".turbo",
        "target",
        "vendor",
        ".cache",
        sandbox.INTERNAL_DIR,
    }
)

#: Finder's droppings. Never what anyone typed a name to find.
SKIP_FILES = frozenset({".DS_Store"})

#: A file this big is a log, a dump or a dataset — content-searching it is the whole budget
#: spent on one answer nobody wanted. 1 MiB, which is what ripgrep's `1M` means, so the two
#: paths draw the line in the same place.
MAX_FILE_BYTES = 1024 * 1024

#: How much of a file decides whether it is text. A NUL in the first 4 KB is ripgrep's test too.
SNIFF_BYTES = 4096

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

#: Wall-clock budget for one search. Long enough for a few thousand files, short enough that a
#: search typed into is never the thing the next one waits behind.
BUDGET_SECONDS = 1.5

#: Lines reported per file. One file that says the word on every line would otherwise be the
#: whole answer, and seeing the file is what the result is for — the viewer shows the rest.
PER_FILE = 3

#: How much of a matching line to send. A minified bundle has lines of a megabyte.
LINE_WIDTH = 200


@dataclass
class Found:
    """What one search turned up, and whether it stopped before the end."""

    results: list[dict] = field(default_factory=list)
    truncated: bool = False
    #: "rg" or "python" — said in the response so a slow search can be explained.
    engine: str = "python"


class SearchError(ValueError):
    """A search that was asked wrongly — a path outside his folder, or nothing to look for."""


def _ripgrep() -> str | None:
    """The ripgrep binary, if this machine has one. A function so a test can take it away."""
    return executables.which("rg")


def search(
    query: str,
    *,
    limit: int = DEFAULT_LIMIT,
    under: str = ".",
    hidden: bool = False,
    budget: float = BUDGET_SECONDS,
) -> Found:
    """Names first, then contents, until `limit` results or `budget` seconds.

    `under` is relative to his workspace root and must stay inside it. `hidden` includes dotted
    files and folders — still never `.git` or his own `.kith` — and is implied by a query that
    itself starts with a dot, because someone typing ".env" is asking for a hidden file.
    """
    needle = (query or "").strip().lower()
    if not needle:
        return Found()
    limit = max(1, min(int(limit), MAX_LIMIT))
    hidden = hidden or needle.startswith(".")
    root = sandbox.root().resolve()
    top = _inside(root, under)
    deadline = time.monotonic() + max(0.05, budget)

    found = Found()
    candidates: list[Path] = []
    for path, is_dir in _walk(top, hidden, deadline):
        if is_dir:
            if needle in path.name.lower():
                found.results.append(_entry(root, path, "name", is_dir=True))
        else:
            if needle in path.name.lower():
                found.results.append(_entry(root, path, "name", is_dir=False))
            candidates.append(path)
        if len(found.results) >= limit:
            found.truncated = True
            return found
    if time.monotonic() >= deadline:
        found.truncated = True
        return found

    left = limit - len(found.results)
    ripgrep = _ripgrep()
    contents: list[dict] | None = None
    if ripgrep:
        contents = _with_ripgrep(ripgrep, root, top, needle, hidden, left, deadline)
        if contents is not None:
            found.engine = "rg"
    if contents is None:
        contents = _with_python(root, candidates, needle, left, deadline)
    found.results.extend(contents[:left])
    found.truncated = len(contents) >= left or time.monotonic() >= deadline
    return found


def _inside(root: Path, under: str) -> Path:
    """`under` as a folder inside his workspace, or a SearchError.

    Resolved before the check, so a symlink in his folder pointing at /etc is refused for where it
    goes rather than allowed for where it sits — the same reason `files.py` gates on resolved paths.
    """
    text = str(under or ".").strip() or "."
    if text.startswith(("/", "~")):
        raise SearchError("give a folder inside his workspace, not an absolute path")
    if any(part == ".." for part in Path(text).parts):
        raise SearchError("that folder is outside his workspace")
    target = (root / text).resolve()
    if not target.is_relative_to(root):
        raise SearchError("that folder is outside his workspace")
    if not target.is_dir():
        raise SearchError(f"there's no folder {text} to search")
    return target


def _skipped_dir(name: str, hidden: bool) -> bool:
    return name in SKIP_DIRS or (name.startswith(".") and not hidden)


def _skipped_file(name: str, hidden: bool) -> bool:
    return name in SKIP_FILES or (name.startswith(".") and not hidden)


def _walk(top: Path, hidden: bool, deadline: float):
    """Every folder and file under `top`, sorted, pruned — and never through a link.

    `os.walk` without `followlinks` already refuses to descend a linked folder; a linked *file*
    is still yielded by name but marked so its contents are never read (see `_readable`).
    """
    for here, dirs, files in os.walk(top, followlinks=False):
        if time.monotonic() >= deadline:
            return
        dirs[:] = sorted(d for d in dirs if not _skipped_dir(d, hidden))
        base = Path(here)
        for name in dirs:
            yield base / name, True
        for name in sorted(files):
            if not _skipped_file(name, hidden):
                yield base / name, False


def _entry(
    root: Path, path: Path, kind: str, *, is_dir: bool, line: int | None = None, text: str | None = None
) -> dict:
    try:
        info = path.lstat()
        size, modified = (0 if is_dir else info.st_size), int(info.st_mtime)
    except OSError:
        size, modified = 0, 0
    out: dict = {
        "path": path.relative_to(root).as_posix(),
        "name": path.name,
        "kind": kind,
        "type": "dir" if is_dir else "file",
        "size": size,
        "modified": modified,
    }
    if line is not None:
        out["line"] = line
        out["text"] = text or ""
    return out


def _clip_line(line: str, needle: str) -> str:
    """The matching line, trimmed — and when it is long, the part with the match in it."""
    text = line.strip()
    if len(text) <= LINE_WIDTH:
        return text
    at = text.lower().find(needle)
    start = max(0, at - LINE_WIDTH // 4) if at >= 0 else 0
    piece = text[start : start + LINE_WIDTH].strip()
    return ("…" if start > 0 else "") + piece + ("…" if start + LINE_WIDTH < len(text) else "")


def _readable(path: Path, root: Path) -> bool:
    """A real file inside his folder, small enough to read. Links are never read through."""
    try:
        if path.is_symlink():
            return False
        return path.stat().st_size <= MAX_FILE_BYTES and path.resolve().is_relative_to(root)
    except OSError:
        return False


def _with_python(root: Path, candidates: list[Path], needle: str, want: int, deadline: float) -> list[dict]:
    out: list[dict] = []
    for path in candidates:
        if len(out) >= want or time.monotonic() >= deadline:
            break
        if not _readable(path, root):
            continue
        try:
            with path.open("rb") as handle:
                head = handle.read(SNIFF_BYTES)
                if b"\0" in head:
                    continue
                data = head + handle.read(MAX_FILE_BYTES)
        except OSError:
            continue
        # Cheap rejection first: most files do not contain the text at all, and lowercasing the
        # whole file once is far cheaper than splitting it into lines to find that out.
        text = data.decode("utf-8", errors="replace")
        if needle not in text.lower():
            continue
        hits = 0
        for number, line in enumerate(text.splitlines(), start=1):
            if needle in line.lower():
                out.append(
                    _entry(root, path, "content", is_dir=False, line=number, text=_clip_line(line, needle))
                )
                hits += 1
                if hits >= PER_FILE or len(out) >= want:
                    break
    return out


def _with_ripgrep(
    binary: str, root: Path, top: Path, needle: str, hidden: bool, want: int, deadline: float
) -> list[dict] | None:
    """Content matches from ripgrep, or None when it could not run — the caller then falls back.

    `--no-ignore` because the Python path does not read `.gitignore` either, and a file being
    findable should not depend on which binary happens to be installed. `--sort path` costs the
    parallelism and buys an answer that does not reshuffle between two identical keystrokes.
    """
    args = [
        binary,
        "--json",
        "--fixed-strings",
        "--ignore-case",
        "--no-ignore",
        "--sort",
        "path",
        "--max-count",
        str(PER_FILE),
        "--max-filesize",
        "1M",
    ]
    if hidden:
        args.append("--hidden")
    for name in sorted(SKIP_DIRS | SKIP_FILES):
        args += ["--glob", f"!{name}"]
    args += ["--", needle, str(top)]

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return []
    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except OSError:
        # Not runnable here — a sandbox that refuses the exec, a binary that moved since startup.
        return None
    # The clock is enforced from outside the read loop: a tree with no matches produces no
    # output, and a blocking readline would sit past the budget waiting for some.
    timer = threading.Timer(remaining, proc.kill)
    timer.start()
    out: list[dict] = []
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            if len(out) >= want:
                break
            hit = _parsed(raw, root, needle)
            if hit is not None:
                out.append(hit)
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
        proc.wait()
    # 0 found, 1 found nothing, a kill is ours. Anything else with nothing to show — a flag this
    # ripgrep is too old for, say — means the search did not happen, so let Python do it.
    if not out and proc.returncode not in (0, 1, -9):
        return None
    return out


def _parsed(raw: bytes, root: Path, needle: str) -> dict | None:
    """One line of `rg --json`, as a result — or None for the begin/end/summary lines."""
    try:
        event = json.loads(raw)
    except ValueError:
        return None
    if event.get("type") != "match":
        return None
    data = event.get("data") or {}
    where = (data.get("path") or {}).get("text")
    if not where:
        return None
    path = Path(where)
    try:
        if not path.resolve().is_relative_to(root):
            return None
    except OSError:
        return None
    # A line that is not valid UTF-8 arrives as base64 `bytes` instead of `text`; shown as the
    # replacement characters it would be anywhere else rather than dropped.
    lines = data.get("lines") or {}
    line = lines.get("text")
    if line is None and "bytes" in lines:
        line = base64.b64decode(lines["bytes"]).decode("utf-8", errors="replace")
    number = data.get("line_number")
    return _entry(
        root,
        path,
        "content",
        is_dir=False,
        line=int(number) if isinstance(number, int) else None,
        text=_clip_line(line or "", needle),
    )
