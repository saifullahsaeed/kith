"""Bringing somebody else's work into the board.

`.kith/tasks/` is committed with the project, so a second person's Kith already writes briefs
into a folder that reaches this one through git. Nothing has ever read them back. Kith A closes
task 42, pushes, Kith B pulls — and B goes on showing it open, because B reads its own database
and the database is a per-machine fact. That is the whole of "multi-user does not work yet".

**Direction.** The folder is where work arrives from other people; the board is where this
machine reads. So this pulls one way — folder into board — and the board's own writes go on
mirroring outward exactly as they did. Not a bidirectional sync, and deliberately not: two
writers on one state is the failure this codebase's thin-shell rule exists to avoid, and a
one-way import has an answer to "which is right" that a merge does not.

**The rule when both changed.** Newest wins, by the `Updated` line the brief now carries. A
brief with no timestamp never wins, and that is not a technicality — it is the case that would
have done damage. Two briefs in a real folder here still say `doing` and `review` for tasks the
board marked `done` weeks ago, and `review` stopped being a status at all. They were written
before their tasks stopped changing, and "the file is the truth" applied naively would have
reverted both.

**A task that is only in the folder is new here.** That is the case this exists for: somebody
else filed it. It comes in with its key, so it is the same task on both machines and stays that
way.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from kith.infra import project_files
from kith.infra.db import repositories as repo
from kith.kernel import session_context

#: Fields a brief is allowed to carry into the board. Not everything a task has: `project_id`
#: and `milestone_id` are this machine's own numbers and mean nothing in someone else's
#: database, and `account` records who filed it, which an import must not rewrite.
_CARRIED = ("goal", "status", "priority", "description")


def preview(path: Path, project_id: int, directory: str) -> dict[str, Any]:
    """What `pull` would do, without doing it.

    The default, and `pull` is what you reach for once you have looked. An import that runs on
    its own and gets something wrong is expensive to unwind; a report that gets something wrong
    costs a sentence. Same shape as `project_files.neutered_by` — describe, never repair — and
    for the same reason.

    It also makes the honest limit visible. Git is not a sync daemon: nothing arrives until
    somebody fetches, so "nothing came in" and "nobody has pulled today" are the same silence
    and mean opposite things. `changed_ago` says which.
    """
    return _walk(path, project_id, directory, apply=False)


def pull(path: Path, project_id: int, directory: str) -> dict[str, Any]:
    """Read the project's folder into the board. Returns what changed, and why.

    Reports rather than logs, because the caller is a turn and the person wants the sentence:
    "three tasks came in from the folder, one of yours was newer so it stayed".
    """
    return _walk(path, project_id, directory, apply=True)


def _walk(path: Path, project_id: int, directory: str, apply: bool) -> dict[str, Any]:
    """One traversal, described or applied. Two of these would drift, and the one that drifted
    would be the report — so the thing you looked at would stop being the thing that happened."""
    empty: dict[str, Any] = {"added": [], "updated": [], "kept": [], "blocked": "", "changed_ago": 0.0}
    if not directory or not Path(directory).is_dir():
        return empty

    # Asked before a single brief is read, and it refuses rather than skipping the bad one: a
    # folder mid-merge is not partly trustworthy. See `project_files.unsettled`.
    blocked = project_files.unsettled(directory)
    if blocked:
        return {**empty, "blocked": blocked}

    board = {int(t["id"]): t for t in repo.tasks.list_tasks(path) if t.get("project_id") == int(project_id)}
    by_key = {str(t.get("key") or ""): t for t in board.values() if t.get("key")}

    added: list[str] = []
    updated: list[str] = []
    kept: list[str] = []
    changed = project_files.last_changed(directory)

    for name, brief in project_files.read_board(directory).items():
        key = str(brief.get("key") or "")
        mine = by_key.get(key) if key else board.get(name if isinstance(name, int) else 0)
        if mine is None:
            if key:
                added.append(_adopt(path, project_id, brief) if apply else str(brief.get("goal") or "")[:40])
            # A brief with no key and no matching row is from a project that used to own this
            # folder — 29 of them in one real case, 17 for tasks deleted long ago. Not ours to
            # resurrect: a task nobody can point at is not a task.
            continue
        differs = {f: brief[f] for f in _CARRIED if brief.get(f) and brief[f] != mine.get(f)}
        if not differs:
            continue
        if _newer(brief.get("updated_at"), mine.get("updated_at")):
            if apply:
                repo.tasks.update_task(path, int(mine["id"]), **differs)
            updated.append(f"{mine['goal'][:40]} ({', '.join(differs)})")
        else:
            kept.append(f"{mine['goal'][:40]} ({', '.join(differs)})")
    return {
        "added": added,
        "updated": updated,
        "kept": kept,
        "blocked": "",
        # How long since anything in the folder was written. Long, with things waiting, usually
        # means nobody has fetched rather than nobody has worked.
        "changed_ago": max(0.0, time.time() - changed) if changed else 0.0,
    }


def _adopt(path: Path, project_id: int, brief: dict[str, Any]) -> str:
    """Bring in a task filed by somebody else, keeping the name it arrived with."""
    made = repo.tasks.add_task(
        path,
        goal=str(brief.get("goal") or "(untitled)"),
        priority=str(brief.get("priority") or "normal"),
        description=str(brief.get("description") or ""),
        status=str(brief.get("status") or "planning"),
        created_by=str(brief.get("created_by") or "kith"),
        project_id=int(project_id),
    )
    # The key and the account come from the brief, not from this machine. Overwritten after the
    # insert rather than passed into it: `add_task` stamps both for work that starts *here*, and
    # this work did not — it started on somebody else's machine and already has a name.
    repo.tasks.set_origin(
        path, int(made["id"]), key=str(brief.get("key") or ""), account=str(brief.get("account") or "")
    )
    for one in brief.get("deliverables") or []:
        # Read out of the brief since it was written and thrown away on the way back in, which
        # is the quietest kind of gap: the evidence a task was finished arrives, is parsed, and
        # does not land.
        where = str(one.get("path") or "")
        repo.tasks.add_deliverable(
            path, int(made["id"]), "file" if where else "text", str(one.get("title") or ""), where
        )
    for item in brief.get("checklist") or []:
        row = repo.tasks.add_checklist_item(path, int(made["id"]), str(item.get("text") or ""))
        if item.get("done"):
            repo.tasks.set_checklist_item(path, int(row["id"]), done=True)
    return str(made.get("goal") or "")[:40]


def _newer(theirs: Any, mine: Any) -> bool:
    """Did the brief change more recently than the row?

    A brief with no `Updated` line is never newer. Those are the ones written before the line
    existed, which is exactly the set most likely to be stale — and a missing timestamp read as
    "now" is how an import undoes work.
    """
    left, right = str(theirs or "").strip(), str(mine or "").strip()
    return bool(left) and left > right


def waiting_here(path: Path) -> str:
    """What the folder of the project in hand is holding, in a sentence. "" when nothing is.

    Attached to `list_tasks`, which is the one moment it matters and the one that is not a hot
    path. Asking the board what is on it is exactly when you want to know that somebody else's
    work is sitting in the folder unread — and `list_tasks` is called deliberately, a few times
    a turn at most, so a walk of fifty markdown files is affordable in a way that the per-round
    prompt assembly is not. `project_binding.adopt` was the other candidate and is the wrong
    one: it fires on every *write*, so an import there would run inside the write that triggered
    it, changing the board underneath the turn doing the changing.

    **Says, never does.** `pull` is still called by nobody. An import that runs on its own and
    gets something wrong is expensive to unwind, and this is the sentence that lets a person
    decide — including the sentence that says the folder cannot be trusted right now.

    Silent on every failure. Reading somebody else's folder is a courtesy; a task list that
    fails because of one would not be.
    """
    try:
        project_id = session_context.current_project()
        if not project_id:
            conversation = session_context.current()
            project_id = repo.conversations.project_of(path, conversation) if conversation else None
        if not project_id:
            return ""
        row = repo.projects.get_project(path, int(project_id))
        directory = str((row or {}).get("directory") or "").strip()
        if not directory:
            return ""
        found = preview(path, int(project_id), directory)
    except Exception:
        return ""

    if found.get("blocked"):
        return (
            f"This project's `.kith/` cannot be read right now — {found['blocked']}. "
            "Nothing has been taken from it. Sort that out before trusting the board here."
        )
    added, updated, kept = found["added"], found["updated"], found["kept"]
    if not (added or updated or kept):
        return ""

    said = []
    if added:
        said.append(f"{len(added)} task(s) in the folder that are not on this board: {', '.join(added[:4])}")
    if updated:
        said.append(f"{len(updated)} where the folder is newer: {', '.join(updated[:4])}")
    if kept:
        said.append(f"{len(kept)} where this board is newer: {', '.join(kept[:4])}")
    quiet = _how_long(found.get("changed_ago") or 0.0)
    return (
        "Somebody else's work is in `.kith/` and has not been taken in. "
        + "; ".join(said)
        + f". The folder last changed {quiet}. Say the word and I will pull it in — "
        "and pull from git first if nobody has today, because nothing arrives on its own."
    )


def _how_long(seconds: float) -> str:
    """Rounded, and never precise: the difference that matters is minutes against days."""
    if seconds < 90:
        return "just now"
    if seconds < 5400:
        return f"{int(seconds // 60)} minutes ago"
    if seconds < 172800:
        return f"{int(seconds // 3600)} hours ago"
    return f"{int(seconds // 86400)} days ago"


def take_it_in(path: Path, project_id: int | None = None) -> dict[str, Any]:
    """Do what `waiting_here` described. The word the sentence invites you to say.

    Split from `pull` so that the thing which finds the project is not the thing which does the
    work — `pull` takes a directory and is testable without a session, and this is what a tool
    calls. Refuses rather than guessing when there is no project in hand: importing into the
    wrong board is not an error anyone would notice until much later.
    """
    if project_id is None:
        project_id = session_context.current_project()
        if not project_id:
            conversation = session_context.current()
            project_id = repo.conversations.project_of(path, conversation) if conversation else None
    if not project_id:
        return {"error": "There is no project in hand, so there is no folder to read."}
    row = repo.projects.get_project(path, int(project_id))
    directory = str((row or {}).get("directory") or "").strip()
    if not directory:
        return {
            "error": f"{(row or {}).get('name') or 'That project'} has no folder, so there is nothing to read."
        }
    return pull(path, int(project_id), directory)
