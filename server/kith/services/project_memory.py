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

#: Where a project keeps what it points *at* — the standard, the ticket, the design, the API
#: docs somebody handed over. Separate from `memory.md` on purpose: memory is what he worked
#: out, references are what he was given, and the two go stale for different reasons. A fact he
#: learned is wrong when the code changes; a link is wrong when somebody moves it.
#:
#: A file rather than a table, and that is the whole reason it exists here. `sources` is a row in
#: a per-machine database, so a reference ingested on one laptop is invisible on the other —
#: which is the same gap `.kith/` was created to close for briefs and memory. This travels with
#: the repository like everything else in the folder.
REFERENCES_FILE = "references.md"

#: The references list stays capped — see below. A project's *memory* is not: it used to be cut
#: to the newest 6,000 characters, which was the right instinct when memory was global (injected
#: into every conversation, its size invisible) and the wrong one now. Two things changed under
#: it. Memory is scoped to its own project, so ai-play's 26k is paid only in ai-play's chats and
#: never in a security-testing one. And its size is a line of its own in the context meter, so it
#: is no longer a cost nobody could see — the person watching the number is the cap now, and a
#: better one than a constant that silently dropped 20,000 characters of hard-won knowledge and
#: kept the *newest* tail, which is the wrong half for someone starting cold. It is the user's
#: file; they decide how much of it is worth carrying, with the meter to decide by.

#: The references list, unlike memory, stays capped — smaller because a reference is a line and
#: not a paragraph. A hundred lines of "here is a link" is a bookmark bar, and a bookmark bar in
#: a system prompt is paid for every round and read by nobody. Say the word if a project's
#: references outgrow this the way its memory did, and it comes off too.
REFERENCES_MAX_CHARS = 3_000

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


def references_path_for(project_dir: str | Path) -> Path:
    """Where this project's references live."""
    return Path(project_dir) / MEMORY_DIR / REFERENCES_FILE


def read(project_dir: str | Path) -> str:
    """This project's memory in full, or "" when it has none yet.

    Uncapped, on purpose — see the note by `REFERENCES_MAX_CHARS`. It was trimmed to the newest
    6,000 characters, which quietly threw away the older two-thirds of a real 26k file and kept
    the end, when the *start* — what the project is, how it runs, how it is laid out — is what a
    cold read needs. Now the whole file is carried, scoped to its own project's chats and shown
    as its own line in the context meter, so its size is a thing the person can see and decide
    about rather than a limit that decided for them.
    """
    target = path_for(project_dir)
    try:
        return target.read_text().strip()
    except (OSError, UnicodeDecodeError):
        return ""


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


def read_references(project_dir: str | Path) -> str:
    """This project's references, or "" when it has none yet.

    Trimmed from the *front* like `read`, and for a weaker reason than memory's: a reference
    list is not strictly chronological, so neither end is obviously the important one. Cutting
    the same end as memory is the tie-breaker — one rule for both files means a person editing
    either knows what happens when it gets long.
    """
    target = references_path_for(project_dir)
    try:
        text = target.read_text().strip()
    except (OSError, UnicodeDecodeError):
        return ""
    if len(text) <= REFERENCES_MAX_CHARS:
        return text
    kept = text[-REFERENCES_MAX_CHARS:]
    return (
        f"[…{len(text) - REFERENCES_MAX_CHARS:,} earlier characters are still in "
        f"{MEMORY_DIR}/{REFERENCES_FILE}. The most recent part:]\n\n{kept}"
    )


def references_block(project_dir: str | Path) -> str:
    """The section of the prompt that carries the references, or one line asking for them.

    **Never scaffolded.** `memory.md` is written with its headings on the day a project is
    linked, because a project always has something to learn about itself. References are
    different: they are *given* to him, and a project may genuinely have none for a week. A
    scaffold would mean fifteen lines of empty headings committed to somebody's repository and
    then read on every turn for the life of the project — paid for constantly, saying nothing.

    So a missing file costs one sentence instead, and the sentence carries the shape the
    scaffold would have. It stops the moment anything is written there, which a scaffold never
    would. Saying nothing at all was the other option and is the worse one: a file nothing
    mentions is a file nobody writes, which is how a feature ships and is never used.

    Silent on a missing *folder* — `block` already says that, loudly and with the fix, and one
    broken link is one problem however many files could not be read because of it.
    """
    where = f"{MEMORY_DIR}/{REFERENCES_FILE}"
    if not Path(project_dir).is_dir():
        return ""
    body = read_references(project_dir)
    if not body:
        return (
            f"This project has no `{where}` yet. When you are handed something the work is held "
            "to — a spec, a ticket, an API contract, a standard, a staging URL — put a line there "
            "saying what it is, where it is, and why you would open it. It travels with the "
            "repository; a link you only have in this conversation does not."
        )
    return (
        f"## What this project points at\n"
        f"From `{where}` — given to you, not worked out by you. Open what the work needs rather "
        f"than assuming what is in it, and add anything you were handed today that the next "
        f"session would have to ask for.\n\n"
        f"{body}"
    )
