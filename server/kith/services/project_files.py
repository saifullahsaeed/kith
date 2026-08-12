"""His own folder *inside* someone's project, so the work survives being handed over.

Everything he produces about a project used to land in one of two wrong places. Before
`base_dir` was fixed it went to `~/Kith/work/` — his private scratch space, invisible to
anyone who cloned the repository, which is how a real project ended up with a memory file
whose own references (`work/srs-review.md`) pointed at nothing a second person could reach.
After that fix it went to the project *root*, which is worse in a different way: his
screenshots and review notes sitting beside somebody's `src/`.

So: `<project>/.kith/`. One folder, obviously his, that commits with the repository. Another
person's Kith clones the repo and finds the memory, the briefs and the working notes already
there — which is the whole point, and is why `.kith/` is *not* in the gitignore any more.

    .kith/
      memory.md          what he knows about this project        (committed)
      README.md          what this folder is, for a human        (committed)
      tasks/07-name.md   a readable brief per task               (committed)
      work/notes.md      working files, findings, drafts         (committed)
      scratch/shot.png   screenshots and throwaways              (ignored)

**The briefs are a mirror, not the board.** Status, priority and ordering stay in the
database, which is the one writer. A brief is a readable record of what a task *is* — enough
for a person, or another Kith, to pick the thread up — and it is written whenever the task
changes rather than being a second place to change it. Two writers on one state is the
failure this project's own thin-shell rule exists to avoid.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

#: The folder inside a project that belongs to him.
KITH_DIR = ".kith"

#: What lives where. `scratch` is the only one the gitignore excludes: it is screenshots, and
#: PNGs in a git history are pure weight.
WORK = "work"
TASKS = "tasks"
SCRATCH = "scratch"

README = """\
# .kith

This folder belongs to Kith — an AI that has been working on this project. It is committed
on purpose: clone the repository and it comes with you, so a later session (or somebody
else's Kith) can pick up where this one left off instead of starting from nothing.

- **memory.md** — what has been learned about this project. Read every time he works here,
  so it is deliberately short. Anything under "Working here" is a standing instruction.
- **tasks/** — a readable brief per task: the goal, how you would know it is done, the
  checklist, the notes. A record, not the live board — status and ordering live in Kith's
  own database on whichever machine is driving.
- **work/** — working files. Findings, drafts, reviews, notes-to-self mid-job.
- **scratch/** — screenshots and throwaways. Gitignored; nothing here is meant to last.

It is all plain text and safe to read, edit or delete. Deleting `memory.md` costs the
project's accumulated knowledge, not its code.
"""


def kith_dir(project_dir: str | Path) -> Path:
    return Path(project_dir) / KITH_DIR


def ensure(project_dir: str | Path) -> Path:
    """Make the folder and its README. Returns the folder.

    The README is for the person who finds `.kith/` in a diff and wonders what it is. A
    committed folder nobody can explain is a folder somebody deletes.
    """
    here = kith_dir(project_dir)
    here.mkdir(parents=True, exist_ok=True)
    readme = here / "README.md"
    if not readme.exists():
        readme.write_text(README)
    return here


def folder_for(project_dir: str | Path, kind: str) -> Path:
    """One of `work`, `tasks`, `scratch`, created on demand."""
    if kind not in (WORK, TASKS, SCRATCH):
        raise ValueError(f"unknown kind {kind!r}")
    here = ensure(project_dir) / kind
    here.mkdir(parents=True, exist_ok=True)
    return here


def slug(text: str, limit: int = 42) -> str:
    """A filename-safe stub of a task's goal, so a directory listing is readable.

    `07-build-the-dashboard-shell.md` tells you what it is; `07.md` makes you open it.
    """
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return cleaned[:limit].strip("-") or "task"


def plan_path(project_dir: str | Path, task_id: int) -> Path:
    """Where the `planning-a-task` skill files a task's plan.

    Built from `kith_dir` rather than `folder_for` on purpose: `folder_for` creates the folder,
    and asking whether a plan exists must not be what brings `.kith/work/` into being.
    """
    return kith_dir(project_dir) / WORK / f"task-{int(task_id)}.md"


def read_plan(project_dir: str | Path, task_id: int) -> str:
    """The plan filed for this task, or "" if there is none.

    Best-effort by design, and the design is the skill's: the path is prose guidance to a model,
    not an enforced location, so a plan filed anywhere else does not show. Silence beats an error
    here — nothing about a task should fail to load because its plan is missing or unreadable.
    """
    try:
        doc = plan_path(project_dir, task_id)
        return doc.read_text(encoding="utf-8") if doc.is_file() else ""
    except OSError:
        return ""


def brief_path(project_dir: str | Path, task_id: int, goal: str) -> Path:
    return folder_for(project_dir, TASKS) / f"{int(task_id):02d}-{slug(goal)}.md"


def write_brief(project_dir: str | Path, task: dict[str, Any]) -> Path | None:
    """Mirror one task to a readable file. Returns the path, or None if it could not be.

    Rewritten wholesale each time rather than patched: the database is the source and this is
    a projection of it, so there is nothing here worth merging and a partial update would be
    a third state to reason about.

    Never raises. A brief that fails to write must not take down the task change that
    triggered it — the board is the thing that matters and this is the copy.
    """
    try:
        task_id = int(task.get("id") or 0)
        if not task_id:
            return None
        path = brief_path(project_dir, task_id, str(task.get("goal") or ""))
        # An old brief under a different slug — the goal was reworded — would otherwise sit
        # there for ever alongside the new one, and a directory of stale duplicates is worse
        # than no directory.
        for stale in path.parent.glob(f"{task_id:02d}-*.md"):
            if stale != path:
                stale.unlink(missing_ok=True)
        path.write_text(_brief(task))
        return path
    except (OSError, ValueError, TypeError):
        return None


def _brief(task: dict[str, Any]) -> str:
    """One task as markdown, written for someone who has not seen the board."""
    goal = str(task.get("goal") or "").strip() or "(untitled)"
    lines = [f"# {goal}", ""]

    facts = [
        ("Status", task.get("status")),
        ("Priority", task.get("priority")),
        ("Due", task.get("due_at")),
    ]
    said = [f"**{label}:** {value}" for label, value in facts if value]
    if said:
        lines += [" · ".join(said), ""]

    done = (task.get("description") or "").strip()
    if done:
        lines += ["## How you know it is done", "", done, ""]

    checklist = task.get("checklist") or []
    if checklist:
        lines += ["## Checklist", ""]
        for item in checklist:
            mark = "x" if item.get("done") else " "
            lines.append(f"- [{mark}] {str(item.get('text') or '').strip()}")
        lines.append("")

    comments = task.get("comments") or []
    if comments:
        lines += ["## Notes", ""]
        for one in comments[-12:]:
            who = "you" if one.get("author") == "user" else "kith"
            body = " ".join(str(one.get("body") or "").split())
            lines.append(f"- **{who}:** {body}")
        lines.append("")

    delivered = task.get("deliverables") or []
    if delivered:
        lines += ["## Delivered", ""]
        for one in delivered:
            title = str(one.get("title") or "").strip()
            where = str(one.get("path") or "").strip()
            lines.append(f"- {title}" + (f" — `{where}`" if where else ""))
        lines.append("")

    lines += [
        "---",
        "",
        "Mirrored from Kith's board. Edit the task in Kith rather than here — this file is "
        "rewritten whenever the task changes.",
    ]
    return "\n".join(lines) + "\n"
