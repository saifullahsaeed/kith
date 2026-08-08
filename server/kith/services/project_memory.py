"""What he knows about *this* project, kept with the project.

Global memory answers "who is this person, what do they care about". It is the wrong place
for "this app's tests are run with `npm test -- --run`, and the store mutates before it
validates". That belongs to the folder, and it should travel with the folder: copy the
project somewhere else and the knowledge goes too; delete the project and it goes with it.

So each project directory gets a ``.kith/memory.md`` — the same idea as a ``CLAUDE.md``
living beside the code it describes.

**It is injected, never fetched.** This is the important decision and it is not a
convenience. A file he has to *remember* to open is a file he will not open: that is the
failure mode behind almost everything that went wrong before this existed — a skill that
told him to call a tool he did not have, a handoff written at the bottom of a file whose top
was all he was shown, a roadmap with no order because ordering was a separate call he never
made. Anything load-bearing has to arrive without him choosing to reach for it.

**Small on purpose.** It is prepended to every request that touches this project, so it is
paid for on every round. A hundred lines of everything-he-ever-noticed is worse than ten
lines of what would have saved him an hour, and the second is what the directive asks for.
"""

from __future__ import annotations

from pathlib import Path

#: Where a project keeps what he has learned about it.
MEMORY_DIR = ".kith"
MEMORY_FILE = "memory.md"

#: How much of it reaches the prompt. Generous enough for real notes about a real codebase,
#: small enough that it cannot quietly become the biggest thing he is sent. Past this the
#: newest part wins, because a project's memory grows at the bottom.
MAX_CHARS = 6_000

#: Written into a new file so the first thing he sees is the shape it should take. An empty
#: file gets filled with whatever occurred to him first; a file with headings gets filled
#: with the things under those headings.
SCAFFOLD = """\
# Project memory

What a later session needs to know about this project and could not work out quickly. Keep
it short — this is read every time, so it earns its place by saving more than it costs.

Facts belong under the headings below. Anything that should hold *whatever* the work is —
a rule, a boundary, a habit — goes in "Working here", and applies to a step taken at three
in the morning with nobody watching exactly as it applies to a conversation.

## Working here
<!-- Standing instructions, not notes. "Run the tests before calling anything done."
     "Never touch migrations/ without asking." "Commit when a checker passes, not per file."
     Written for someone who has no other context and cannot ask. -->

## How to run it
<!-- the exact commands: install, dev, test, build. Verified, not assumed. -->

## How it is laid out
<!-- where the important things live, so nobody greps for them twice. -->

## Decisions
<!-- what was chosen and why, so it is not quietly undone later. -->

## Gotchas
<!-- what wasted time once and should never waste it again. -->
"""


def path_for(project_dir: str | Path) -> Path:
    """Where this project's memory lives."""
    return Path(project_dir) / MEMORY_DIR / MEMORY_FILE


def read(project_dir: str | Path) -> str:
    """This project's memory, or "" when it has none yet.

    Trimmed from the front when over-long. A project's memory is appended to as it is
    learned, so the recent end is the part that reflects the code as it stands now — the
    same reasoning as the handoff at the bottom of a working file, which was being cut from
    the wrong end for exactly this reason.
    """
    target = path_for(project_dir)
    try:
        text = target.read_text().strip()
    except (OSError, UnicodeDecodeError):
        return ""
    if len(text) <= MAX_CHARS:
        return text
    kept = text[-MAX_CHARS:]
    return (
        f"[…{len(text) - MAX_CHARS:,} earlier characters are still in "
        f"{MEMORY_DIR}/{MEMORY_FILE}. The most recent part:]\n\n{kept}"
    )


def ensure(project_dir: str | Path) -> Path:
    """Create the file with its scaffold if it is not there. Returns the path."""
    target = path_for(project_dir)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(SCAFFOLD)
    return target


def block(project_dir: str | Path, name: str = "") -> str:
    """The section of the prompt that carries it, or "" when there is nothing to carry.

    Names the file as well as showing it, because reading it is only half of what he needs
    to do with it — the other half is adding to it, and he cannot do that without the path.
    """
    where = f"{MEMORY_DIR}/{MEMORY_FILE}"
    label = f" for {name}" if name else ""

    # A folder that is not there is a different problem from a folder with nothing written in
    # it yet, and telling them apart is the whole point of this branch. Conflated, they read
    # as "no memory yet" — which is what actually happened: a project stayed linked to a
    # folder that had been deleted, so 3,068 characters of hard-won project memory sat unread
    # on disk while every turn was told there was none and invited to start a fresh one. That
    # invitation is the dangerous part. He would have written the new file into a folder that
    # does not exist, or into his own, and the real one would have gone on being invisible.
    #
    # Nothing else notices, either: `base_dir()` falls back to his own folder without
    # complaint, so the work simply happens in the wrong place. This message is the only
    # place a broken link is ever said out loud, which is why it names the path and the fix.
    here = Path(project_dir)
    if not here.is_dir():
        return (
            f"⚠ This project{label} is linked to `{here}`, and that folder is not there. "
            "Anything you write with a relative path will land in your own folder instead, "
            "and whatever the project already knew about itself cannot be read. Do not start "
            "a new memory file — find where the work actually lives and re-link it with "
            "`link_folder`, or tell your person the link is broken."
        )

    body = read(project_dir)
    if not body:
        return (
            f"This project{label} has no `{where}` yet. When you learn something a later "
            "session would need — the command that actually runs the tests, where the real "
            "logic lives, a decision worth not undoing — write it there."
        )
    return (
        f"## What you already know about this project{label}\n"
        f"From `{where}`. Anything under **Working here** is an instruction and holds for "
        f"this whole project — it is not a note to consider, and it outranks your own habits "
        f"where they differ. The rest is what has been learned so far: keep it true, fix what "
        f"turns out to be wrong, and add what would have saved you time today.\n\n"
        f"{body}"
    )
