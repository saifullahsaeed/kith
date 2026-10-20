"""A folder's own rules — its `CLAUDE.md` and `AGENTS.md` — arriving with the first file opened in it.

Repositories carry instructions for whoever works in them, and Kith read none of them unless
he went looking. Measured on sadeef-v2, which has a root `CLAUDE.md` and fifty-two `AGENTS.md`
files whose first line is "read this before touching anything": every builder there opened by
reading the chain by hand — "I'll start by reading the required AGENTS files" — out of a budget
of thirty rounds, and nineteen of twenty ran out of rounds before writing anything.

Claude Code's answer, copied here: the instructions for a folder come with the first file you
open under it. Every `CLAUDE.md` and `AGENTS.md` between the repository's top and the file, top
first, each shown once.

**Once per conversation.** Its history replays old tool results until a fold — re-attaching a
15k-character root on every turn would be a second copy each time. So an attached file is
recorded in the conversation's file ledger (`touched.shown`) exactly as a read of it would be,
and a file the ledger already holds at its current version is not attached again. Edit one and it
changes version, and the next read brings it back. An agent is a conversation of its own, so it
has a ledger of its own and is shown the rules it has not seen, whatever its handler has read.

Capped twice, because some of these files are enormous (sadeef's largest is 67k characters): each
file to :data:`FILE_CHARS`, cut at a line and said so, and each result to :data:`RESULT_CHARS`,
with anything that did not fit named rather than dropped — and left unshown, so the next read
under that folder brings it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from kith.infra import workspace
from kith.kernel import session_context
from kith.services import touched

#: The file names a folder's instructions go by. Both, because tools disagree: Claude Code reads
#: `CLAUDE.md`, most others `AGENTS.md`, and a repository written for both often has both.
NAMES = ("CLAUDE.md", "AGENTS.md")

#: The most of one file attached. A root constitution is a few thousand characters; this is room
#: for the long ones without letting a 67k-character spec ride along whole.
FILE_CHARS = 20_000

#: The most attached to one result, whatever it read.
RESULT_CHARS = 40_000

#: How far up a folder walk may go before giving up on finding the repository's top.
_MOST_LEVELS = 40

#: Key in `session_context.turn_notes()`: the instruction files this turn has been shown.
_SHOWN = "instructions_shown"


def chain(target: Path) -> list[Path]:
    """Every instruction file that governs ``target``, from the repository's top down to it.

    The walk stops at the first folder holding `.git` — a file in a worktree copy, a folder in a
    clone — which is the repository's top. Outside any repository it stops below the home
    folder: `~/CLAUDE.md` is not anybody's rules for `~/Downloads`.
    """
    start = target if target.is_dir() else target.parent
    home = Path.home()
    folders: list[Path] = []
    here = start
    for _ in range(_MOST_LEVELS):
        if here == home:
            break
        folders.append(here)
        if (here / ".git").exists() or here.parent == here:
            break
        here = here.parent
    found: list[Path] = []
    for folder in reversed(folders):
        for name in NAMES:
            candidate = folder / name
            if candidate.is_file():
                found.append(candidate)
    return found


def attach(agent_db: Path, wanted: list[str]) -> str:
    """The rules for the folders ``wanted`` is in that this work has not been shown yet, or "".

    Never raises: this is a remark beside a result that has already been produced, and a folder
    it cannot read must not turn a good read into a failed call.
    """
    try:
        return _attach(agent_db, wanted)
    except Exception:
        return ""


def _attach(agent_db: Path, wanted: list[str]) -> str:
    shown_now: set[str] = session_context.turn_notes().setdefault(_SHOWN, set())
    targets = [Path(workspace.resolve(one)) for one in wanted if str(one or "").strip()]
    asked = {_real(one) for one in targets}

    due: list[Path] = []
    contents: set[str] = set()
    for target in targets:
        for found in chain(target):
            real = _real(found)
            if real in shown_now:
                continue
            shown_now.add(real)
            text = _read(found)
            if not text.strip():
                continue
            digest = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()
            if digest in contents:
                # The same rules under the other name — a CLAUDE.md that is a copy of the
                # AGENTS.md beside it. Recorded as seen, so a later turn does not mistake it for
                # something new once its twin has been skipped as already known.
                touched.shown(agent_db, str(found))
                continue
            contents.add(digest)
            if real in asked:
                continue  # it is the file being read — already in the result
            if touched.has_seen(agent_db, str(found)):
                continue
            due.append(found)
    if not due:
        return ""

    blocks: list[str] = []
    left_out: list[Path] = []
    used = 0
    for found in due:
        text, extent = _clipped(_read(found).rstrip())
        if left_out or (blocks and used + len(text) > RESULT_CHARS):
            # Once one does not fit, nothing below it goes either: a deeper folder's rules
            # without the ones above them would read as the whole story.
            left_out.append(found)
            shown_now.discard(_real(found))
            continue
        used += len(text)
        blocks.append(f"===== {_named(found)} =====\n{text}")
        if extent:
            blocks[-1] += f"\n… (lines {extent} shown — read `{found}` for the rest)"
        touched.shown(agent_db, str(found), extent)

    head = (
        "[Rules for this part of the code — the CLAUDE.md / AGENTS.md files between the "
        "repository's top and what you just opened, top first. They come with the first file you "
        "open under each folder; you do not need to open them yourself. Follow them.]"
    )
    tail = ""
    if left_out:
        tail = "\n\nAlso here, not shown for room — read before changing code under them:\n" + "\n".join(
            f"- `{found}`" for found in left_out
        )
    return head + "\n\n" + "\n\n".join(blocks) + tail


def _read(found: Path) -> str:
    try:
        return found.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _clipped(text: str) -> tuple[str, str]:
    """``text`` cut at a line to fit :data:`FILE_CHARS`, and the lines kept — "" when whole.

    The extent is in the ledger's own terms (``1-120 of 900``) so the manifest says "read only
    part of it" about a file that was cut, rather than "whole".
    """
    if len(text) <= FILE_CHARS:
        return text, ""
    lines = text.splitlines(keepends=True)
    kept, used = 0, 0
    for line in lines:
        if used + len(line) > FILE_CHARS:
            break
        used += len(line)
        kept += 1
    if not kept:  # one line longer than the whole allowance
        return text[:FILE_CHARS], f"1-1 of {len(lines)}"
    return "".join(lines[:kept]).rstrip("\n"), f"1-{kept} of {len(lines)}"


def _named(found: Path) -> str:
    """The path as he would type it: relative to where he is working when it is under it."""
    try:
        return str(found.relative_to(workspace.paths.base_dir()))
    except ValueError:
        return str(found)


def _real(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path)
