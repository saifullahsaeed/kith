"""What a codebase contains, in one read.

Dropped into an unfamiliar repository, the only moves he had were `list_files` — which shows
names and tells you nothing about what is in them — and a series of guesses at `grep`. Both
are cheap individually and ruinous in aggregate: twenty rounds of orientation against a turk
that gets sixteen, and every file he opened along the way is still in the prompt at the end.
The job dies during the reconnaissance.

A repo map is the whole shape at once: the files that matter, each with the definitions in it,
inside a fixed token budget. It is the thing you would want someone to tell you on your first
day, and it costs about as much as opening two files.

**The budget is the design.** A map that grows with the repository is not a map, it is the
repository. So the ceiling is set first and the content competes for it: files are ranked,
and the ones that lose are counted rather than shown — "and 214 more files" is a true and
useful sentence, where quietly stopping at the fortieth is neither. What gets cut is always
said out loud, because a map that looks complete and is not is worse than an obviously
partial one.

**Ranking, and why not PageRank.** Aider's version builds a reference graph and ranks by
importance, which is better than what is here and needs the whole repo parsed to produce a
single number. This ranks on what a directory listing already knows — how recently a file
changed, whether it sits near the root, how many definitions it holds, and whether its name
is one of the names projects use for their entry points. It is a heuristic and it is cheap,
and it is right often enough to put `main.py` above `test_helpers_generated.py`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kith.engine.code import outline

#: Folders that are never the answer. Walking them is slow and mapping them is noise: a
#: node_modules with 40,000 files would consume the whole budget and describe none of the
#: user's code.
SKIP_DIRS = frozenset(
    {
        "node_modules",
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "env",
        "dist",
        "build",
        "out",
        "target",
        ".next",
        ".nuxt",
        ".cache",
        "coverage",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "vendor",
        "Pods",
        ".terraform",
        "site-packages",
        ".tox",
        ".idea",
        ".vscode",
        "__snapshots__",
    }
)

#: Names that usually mean "start here". A small nudge, not a decision — enough to break a
#: tie in favour of the file someone would actually open first.
_ENTRY_NAMES = frozenset(
    {
        "main",
        "index",
        "app",
        "server",
        "cli",
        "__init__",
        "mod",
        "lib",
        "core",
        "router",
        "routes",
        "config",
        "settings",
        "schema",
        "models",
    }
)

#: Roughly how many characters of map to produce. Converted from a token figure at the
#: repo's own ratio: `llm.budget.SEED_CHARS_PER_TOKEN`.
DEFAULT_TOKENS = 3_000

#: The most a caller may ask for, however large a number it passes. Above about this a map
#: stops being cheaper than reading the files it describes, which is the entire reason to
#: have one.
MAX_BUDGET_TOKENS = 12_000

#: Signatures are clipped harder here than in an outline. At map scale a full parameter list
#: is not what the reader is deciding on — they are deciding which file to open — and a
#: hundred-character generic signature costs the map two other files. `outline` shows the
#: whole thing for the one file that turns out to matter.
_MAP_SIGNATURE_CHARS = 72

#: Stop walking here however deep the tree goes. A monorepo is wide, not infinitely deep, and
#: a runaway symlink loop is a real thing on a real machine.
MAX_DEPTH = 12

#: Never stat more than this many files. On a very large repository the walk itself is the
#: cost, long before anything is parsed.
MAX_FILES_SCANNED = 20_000


class RepoMapError(Exception):
    """Raised when a folder cannot be mapped, with a reason worth reading."""


#: Most symbols shown for any one file in a map. A 90-method service class would otherwise
#: spend the entire budget describing itself, and the map's job is breadth — `outline` is one
#: call away for the depth.
MAX_SYMBOLS_PER_FILE = 14


@dataclass
class Entry:
    path: str
    score: float
    symbols: list[dict[str, Any]]
    lines: int
    hidden: int = 0


def _worth_showing(symbols: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Trim one file's symbols to the map's per-file allowance.

    Top-level definitions first, and all of them if they fit: they are the file's interface,
    and a map that lists six methods of one class while omitting four sibling classes has
    described the file badly. Methods fill whatever room is left, in the order they appear —
    reading order, not importance order, because scrambling them costs the reader more than
    the ranking gains.
    """
    top = [one for one in symbols if one["depth"] == 0]
    nested = [one for one in symbols if one["depth"] != 0]

    if len(top) >= MAX_SYMBOLS_PER_FILE:
        return top[:MAX_SYMBOLS_PER_FILE], len(symbols) - MAX_SYMBOLS_PER_FILE

    room = MAX_SYMBOLS_PER_FILE - len(top)
    keep = {id(one) for one in top} | {id(one) for one in nested[:room]}
    chosen = [one for one in symbols if id(one) in keep]
    return chosen, len(symbols) - len(chosen)


def _skip(part: str) -> bool:
    return part in SKIP_DIRS or (part.startswith(".") and part not in (".", ".."))


def _candidates(root: Path) -> list[Path]:
    """Every source file under `root` we could read the shape of."""
    found: list[Path] = []
    stack = [(root, 0)]
    scanned = 0
    while stack:
        here, depth = stack.pop()
        if depth > MAX_DEPTH:
            continue
        try:
            entries = list(here.iterdir())
        except OSError:
            continue
        for item in entries:
            scanned += 1
            if scanned > MAX_FILES_SCANNED:
                return found
            name = item.name
            try:
                if item.is_dir():
                    if not _skip(name) and not item.is_symlink():
                        stack.append((item, depth + 1))
                    continue
            except OSError:
                continue
            if outline.language_for(name) is not None:
                found.append(item)
    return found


def _score(path: Path, root: Path, now: float) -> float:
    """How likely this file is to be one someone wants to know about.

    Recency dominates because it is the strongest available signal about what the work is
    currently about, and it is the one thing here that is not a guess about naming.
    """
    try:
        stat = path.stat()
    except OSError:
        return 0.0

    try:
        depth = len(path.relative_to(root).parts) - 1
    except ValueError:
        depth = 0

    days = max(0.0, (now - stat.st_mtime) / 86_400)
    # Touched today ~3.0, a week ago ~1.5, a year ago ~0.1. Log rather than linear: the
    # difference between one day and eight matters; between 300 and 400 it does not.
    recency = 3.0 / (1.0 + days) ** 0.5

    shallow = max(0.0, 1.5 - 0.35 * depth)
    entry = 1.0 if path.stem.lower() in _ENTRY_NAMES else 0.0
    # A file with nothing in it is not interesting however recent, and a 4,000-line file is
    # not four times as interesting as a 1,000-line one.
    substance = min(1.5, stat.st_size / 8_000)
    tests = -0.6 if ("test" in path.stem.lower() or "spec" in path.stem.lower()) else 0.0
    return recency + shallow + entry + substance + tests


def build(
    root: str | Path,
    budget_tokens: int = DEFAULT_TOKENS,
    focus: str = "",
) -> dict[str, Any]:
    """Map a folder within a token budget.

    `focus` is a substring; when given, files whose path contains it are ranked first. That is
    the difference between "show me this repo" and "show me the auth code in this repo", and
    it costs one `in` per file rather than a second mechanism.
    """
    here = Path(str(root)).expanduser()
    if not here.is_dir():
        raise RepoMapError(f"{root} is not a folder to map")

    files = _candidates(here)
    if not files:
        raise RepoMapError(
            f"no source files under {root} that I can read the shape of — "
            "list_files will show you what is actually there."
        )

    now = time.time()
    wanted = focus.strip().lower()
    ranked = sorted(
        files,
        key=lambda path: -(2.5 if wanted and wanted in str(path).lower() else 0.0) - _score(path, here, now),
    )

    budget_chars = int(budget_tokens * 3.7)
    entries: list[Entry] = []
    spent = 0
    parsed = 0
    for path in ranked:
        if spent >= budget_chars:
            break
        # Parsing is the expensive part, so it happens only for files that are still in the
        # running — not for all 20,000 before ranking.
        try:
            one = outline.of_file(path)
        except outline.OutlineError:
            continue
        except Exception:
            # A file that will not parse is one file missing from the map, not a reason to have
            # no map. `OutlineError` is the expected refusal (an unsupported language, a
            # binary); the broad catch is for a parser that crashes on one pathological file,
            # which on a 20,000-file tree is a matter of time.
            continue
        parsed += 1
        all_symbols = one.get("symbols") or []
        if not all_symbols:
            continue
        symbols, hidden = _worth_showing(all_symbols)
        try:
            shown = str(path.relative_to(here))
        except ValueError:
            shown = str(path)
        cost = len(shown) + sum(
            min(len(s["signature"] or s["name"]), _MAP_SIGNATURE_CHARS) + 8 for s in symbols
        )
        if spent + cost > budget_chars and entries:
            break
        entries.append(
            Entry(path=shown, score=0.0, symbols=symbols, lines=one.get("lines", 0), hidden=hidden)
        )
        spent += cost

    return {
        "root": str(here),
        "files_shown": len(entries),
        "files_found": len(files),
        "entries": [
            {"path": e.path, "lines": e.lines, "symbols": e.symbols, "hidden": e.hidden} for e in entries
        ],
        "focus": focus or "",
    }


def render(mapped: dict[str, Any]) -> str:
    """The map as something to read.

    Signatures are dropped for anything nested — a method list is for knowing what exists, and
    at map scale the parameter lists are what push a useful map past its budget. The outline
    tool is one call away when the detail matters.
    """
    entries = mapped.get("entries") or []
    if not entries:
        return f"{mapped['root']} — nothing with definitions in it that I can read."

    out: list[str] = []
    for entry in entries:
        out.append(f"{entry['path']}  ({entry['lines']} lines)")
        for symbol in entry["symbols"]:
            if symbol["depth"] == 0:
                text = symbol["signature"] or symbol["name"]
                if len(text) > _MAP_SIGNATURE_CHARS:
                    text = text[: _MAP_SIGNATURE_CHARS - 1].rstrip() + "…"
                out.append(f"  {symbol['line']:>5}  {text}")
            else:
                out.append(
                    f"  {symbol['line']:>5}  {'  ' * symbol['depth']}{symbol['kind']} {symbol['name']}"
                )
        if entry.get("hidden"):
            out.append(f"         … {entry['hidden']} more in this file — `outline` it to see them")
        out.append("")

    shown, found = mapped["files_shown"], mapped["files_found"]
    head = f"{mapped['root']} — {shown} of {found} source files"
    if mapped.get("focus"):
        head += f", ranked for '{mapped['focus']}'"
    # Said out loud, always. A truncated map that looks whole is how you conclude a function
    # does not exist because it was in the two hundred and fifteenth file.
    if found > shown:
        out.append(f"and {found - shown} more source files not shown — narrow with `focus`, or grep.")
    return head + "\n\n" + "\n".join(out).rstrip()
