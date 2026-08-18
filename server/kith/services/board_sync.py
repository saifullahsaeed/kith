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

from pathlib import Path
from typing import Any

from kith.infra import project_files
from kith.infra.db import repositories as repo

#: Fields a brief is allowed to carry into the board. Not everything a task has: `project_id`
#: and `milestone_id` are this machine's own numbers and mean nothing in someone else's
#: database, and `account` records who filed it, which an import must not rewrite.
_CARRIED = ("goal", "status", "priority", "description")


def pull(path: Path, project_id: int, directory: str) -> dict[str, Any]:
    """Read the project's folder into the board. Returns what changed, and why.

    Reports rather than logs, because the caller is a turn and the person wants the sentence:
    "three tasks came in from the folder, one of yours was newer so it stayed".
    """
    if not directory or not Path(directory).is_dir():
        return {"added": [], "updated": [], "kept": []}

    board = {int(t["id"]): t for t in repo.tasks.list_tasks(path) if t.get("project_id") == int(project_id)}
    by_key = {str(t.get("key") or ""): t for t in board.values() if t.get("key")}

    added: list[str] = []
    updated: list[str] = []
    kept: list[str] = []

    for name, brief in project_files.read_board(directory).items():
        key = str(brief.get("key") or "")
        mine = by_key.get(key) if key else board.get(name if isinstance(name, int) else 0)
        if mine is None:
            if key:
                added.append(_adopt(path, project_id, brief))
            # A brief with no key and no matching row is from a project that used to own this
            # folder — 29 of them in one real case, 17 for tasks deleted long ago. Not ours to
            # resurrect: a task nobody can point at is not a task.
            continue
        changed = {f: brief[f] for f in _CARRIED if brief.get(f) and brief[f] != mine.get(f)}
        if not changed:
            continue
        if _newer(brief.get("updated_at"), mine.get("updated_at")):
            repo.tasks.update_task(path, int(mine["id"]), **changed)
            updated.append(f"{mine['goal'][:40]} ({', '.join(changed)})")
        else:
            kept.append(f"{mine['goal'][:40]} ({', '.join(changed)})")
    return {"added": added, "updated": updated, "kept": kept}


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
