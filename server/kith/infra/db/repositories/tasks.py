"""Tasks, and everything hanging off one: comments, checklist, deliverables."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from kith.domain.enums import TASK_ACTIVE, TASK_PRIORITIES, TASK_STATUSES
from kith.infra.db.engine import as_dict, session
from kith.infra.db.models import ChecklistItem, Deliverable, Milestone, Project, Task, TaskComment
from kith.infra.db.support import utc_now_iso

# Rank for sorting: high first, then normal, then low.
_PRIORITY_RANK = {"high": 0, "normal": 1, "low": 2}

# A task in one of these is finished as far as roll-up is concerned — "dropped"
# counts as settled, or abandoning one task would keep its milestone open forever.
_SETTLED = ("done", "dropped")

_DELIVERABLE_KINDS = ("text", "file", "link")


# --------------------------------------------------------------------------- #
# Tasks
# --------------------------------------------------------------------------- #


def add_task(
    path: Path,
    goal: str,
    priority: str = "normal",
    due_at: str | None = None,
    description: str = "",
    status: str = "todo",
    created_by: str = "kith",
    project_id: int | None = None,
    milestone_id: int | None = None,
) -> dict:
    if priority not in TASK_PRIORITIES:
        priority = "normal"
    if status not in TASK_STATUSES:
        status = "todo"
    now = utc_now_iso()
    with session(path) as db:
        # A task under a milestone belongs to that milestone's project. Resolved in
        # the same transaction as the insert, so the two can't disagree.
        if milestone_id and not project_id:
            milestone = db.get(Milestone, milestone_id)
            if milestone:
                project_id = milestone.project_id
        row = Task(
            goal=goal,
            status=status,
            priority=priority,
            due_at=due_at,
            description=description,
            created_by=created_by,
            project_id=project_id,
            milestone_id=milestone_id,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.flush()
        return as_dict(row)


def set_task_milestone(path: Path, task_id: int, milestone_id: int | None) -> dict | None:
    """Link a task to a milestone (and inherit the milestone's project)."""
    with session(path) as db:
        row = db.get(Task, task_id)
        if row is None:
            return None
        row.milestone_id = milestone_id
        if milestone_id:
            milestone = db.get(Milestone, milestone_id)
            if milestone:
                row.project_id = milestone.project_id
        row.updated_at = utc_now_iso()
        db.flush()
        return as_dict(row)


def set_task_project(path: Path, task_id: int, project_id: int | None) -> dict | None:
    with session(path) as db:
        row = db.get(Task, task_id)
        if row is None:
            return None
        row.project_id = project_id
        row.updated_at = utc_now_iso()
        db.flush()
        return as_dict(row)


def list_tasks(path: Path, status: str | None = None) -> list[dict]:
    """All tasks, ordered by priority (high first) then most-recently-updated.

    Sorted in Python because priority is a word, not a number — ordering by the
    column would give alphabetical (high, low, normal), which is wrong.
    """
    query = select(Task)
    if status:
        query = query.where(Task.status == status)
    with session(path) as db:
        tasks = [as_dict(row) for row in db.scalars(query).all()]
    tasks.sort(key=lambda task: (_PRIORITY_RANK.get(task.get("priority"), 1), _newest_first(task)))
    return tasks


def active_tasks(path: Path) -> list[dict]:
    """What he should actually work next, highest priority first.

    Three things are excluded, and the third is the one that gives a roadmap teeth:

    * anything not todo/doing — nothing to do on it;
    * anything under a done or paused project, so a finished project lets him rest;
    * anything under a milestone whose predecessors are not finished. That last one is why
      milestones exist at all. Before it, the roadmap described progress after the fact
      while raw task priority decided the order — so a "roadmap" imposed nothing and there
      was no reason to keep one. Now a milestone that is waiting keeps its tasks waiting
      with it, and the order someone laid out is the order the work happens in.

    Tasks with no milestone are unaffected: a one-off errand should not need a roadmap.
    """
    from kith.infra.db.repositories.projects import blocked_milestone_ids

    with session(path) as db:
        parked = set(db.scalars(select(Project.id).where(Project.status != "active")).all())
    blocked = blocked_milestone_ids(path)
    return [
        task
        for task in list_tasks(path)
        if task["status"] in TASK_ACTIVE
        and task.get("project_id") not in parked
        and task.get("milestone_id") not in blocked
    ]


def waiting_on_the_roadmap(path: Path) -> list[dict]:
    """Actionable tasks held back only because their milestone is waiting.

    Kept separate from ``active_tasks`` so "there is nothing to do" and "there is plenty to
    do but it is not this milestone's turn" are different sentences. He needs to be able to
    say which, and so does the interface — a blocked board that looks identical to an empty
    one is how someone concludes the thing is broken.
    """
    from kith.infra.db.repositories.projects import blocked_milestone_ids

    with session(path) as db:
        parked = set(db.scalars(select(Project.id).where(Project.status != "active")).all())
    blocked = blocked_milestone_ids(path)
    return [
        task
        for task in list_tasks(path)
        if task["status"] in TASK_ACTIVE
        and task.get("project_id") not in parked
        and task.get("milestone_id") in blocked
    ]


def update_task(
    path: Path,
    task_id: int,
    status: str | None = None,
    goal: str | None = None,
    priority: str | None = None,
    due_at: str | None = None,
    description: str | None = None,
) -> dict | None:
    if status is not None and status not in TASK_STATUSES:
        raise ValueError(f"status must be one of {TASK_STATUSES}")
    if priority is not None and priority not in TASK_PRIORITIES:
        raise ValueError(f"priority must be one of {TASK_PRIORITIES}")

    with session(path) as db:
        row = db.get(Task, task_id)
        if row is None:
            return None
        if status is not None:
            row.status = status
        if goal is not None:
            row.goal = goal
        if priority is not None:
            row.priority = priority
        if due_at is not None:
            row.due_at = due_at
        if description is not None:
            row.description = description
        row.updated_at = utc_now_iso()
        db.flush()

        if status == "done":
            # Same transaction as the status change: a crash between the two would
            # otherwise leave a finished milestone whose last task looks open.
            _roll_up(db, row)
            db.flush()
        return as_dict(row)


def _roll_up(db: Session, task: Task) -> None:
    """When a task is finished, roll the ladder up: if it clears its milestone,
    mark the milestone done; if that clears the project's milestones, mark the
    project done. Runs inside the caller's transaction."""
    now = utc_now_iso()
    milestone_id = task.milestone_id
    project_id = task.project_id

    if milestone_id:
        if _open_count(db, Task, Task.milestone_id == milestone_id) == 0:
            milestone = db.get(Milestone, milestone_id)
            if milestone and milestone.status != "done":
                milestone.status = "done"
                milestone.updated_at = now
            # A task linked to a milestone but not a project still rolls up.
            if milestone and not project_id:
                project_id = milestone.project_id

    if not project_id:
        return

    total_milestones = (
        db.scalar(select(func.count()).select_from(Milestone).where(Milestone.project_id == project_id)) or 0
    )
    open_milestones = (
        db.scalar(
            select(func.count())
            .select_from(Milestone)
            .where(Milestone.project_id == project_id, Milestone.status != "done")
        )
        or 0
    )
    open_tasks = _open_count(db, Task, Task.project_id == project_id)

    # Done when the roadmap is cleared, or — for a project run purely off tasks with
    # no milestones — when nothing is left to do under it.
    cleared = (total_milestones > 0 and open_milestones == 0) or (total_milestones == 0 and open_tasks == 0)
    if not cleared:
        return
    project = db.get(Project, project_id)
    # Only 'active' → 'done': a paused or archived project stays as its person left it.
    if project and project.status == "active":
        project.status = "done"
        project.updated_at = now


def _open_count(db: Session, model: type[Task], *conditions) -> int:
    """How many unsettled tasks match — the roll-up's only real question."""
    return int(
        db.scalar(select(func.count()).select_from(model).where(*conditions, model.status.not_in(_SETTLED)))
        or 0
    )


def _newest_first(task: dict) -> str:
    """Sort key that puts most-recently-updated first (invert the ISO string)."""
    stamp = task.get("updated_at") or task.get("created_at") or ""
    return "".join(chr(255 - ord(character)) for character in stamp)


def delete_task(path: Path, task_id: int) -> bool:
    with session(path) as db:
        return db.execute(delete(Task).where(Task.id == task_id)).rowcount > 0


# --------------------------------------------------------------------------- #
# Task detail: comments (two-way), checklist, deliverables
# --------------------------------------------------------------------------- #


def add_task_comment(path: Path, task_id: int, author: str, body: str) -> dict:
    with session(path) as db:
        row = TaskComment(task_id=task_id, author=author, body=body, created_at=utc_now_iso())
        db.add(row)
        db.flush()
        return as_dict(row)


def list_task_comments(path: Path, task_id: int) -> list[dict]:
    query = select(TaskComment).where(TaskComment.task_id == task_id).order_by(TaskComment.id.asc())
    with session(path) as db:
        return [as_dict(row) for row in db.scalars(query).all()]


def delete_task_comment(path: Path, comment_id: int) -> bool:
    with session(path) as db:
        return db.execute(delete(TaskComment).where(TaskComment.id == comment_id)).rowcount > 0


def add_checklist_item(path: Path, task_id: int, text: str) -> dict:
    with session(path) as db:
        next_index = db.scalar(
            select(func.coalesce(func.max(ChecklistItem.order_index), -1) + 1).where(
                ChecklistItem.task_id == task_id
            )
        )
        row = ChecklistItem(
            task_id=task_id,
            text=text,
            done=0,
            order_index=int(next_index or 0),
            created_at=utc_now_iso(),
        )
        db.add(row)
        db.flush()
        return as_dict(row)


def list_checklist(path: Path, task_id: int) -> list[dict]:
    query = (
        select(ChecklistItem)
        .where(ChecklistItem.task_id == task_id)
        .order_by(ChecklistItem.order_index, ChecklistItem.id)
    )
    with session(path) as db:
        return [as_dict(row) for row in db.scalars(query).all()]


def set_checklist_item(
    path: Path, item_id: int, done: bool | None = None, text: str | None = None
) -> dict | None:
    if done is None and text is None:
        return None  # nothing asked for; don't touch the row
    with session(path) as db:
        row = db.get(ChecklistItem, item_id)
        if row is None:
            return None
        if done is not None:
            row.done = 1 if done else 0
        if text is not None:
            row.text = text
        db.flush()
        return as_dict(row)


def delete_checklist_item(path: Path, item_id: int) -> bool:
    with session(path) as db:
        return db.execute(delete(ChecklistItem).where(ChecklistItem.id == item_id)).rowcount > 0


def add_deliverable(path: Path, task_id: int, kind: str, title: str, content: str) -> dict:
    if kind not in _DELIVERABLE_KINDS:
        kind = "text"
    with session(path) as db:
        row = Deliverable(task_id=task_id, kind=kind, title=title, content=content, created_at=utc_now_iso())
        db.add(row)
        db.flush()
        return as_dict(row)


def list_deliverables(path: Path, task_id: int) -> list[dict]:
    query = select(Deliverable).where(Deliverable.task_id == task_id).order_by(Deliverable.id)
    with session(path) as db:
        return [as_dict(row) for row in db.scalars(query).all()]


def delete_deliverable(path: Path, deliverable_id: int) -> bool:
    with session(path) as db:
        return db.execute(delete(Deliverable).where(Deliverable.id == deliverable_id)).rowcount > 0


def task_detail(path: Path, task_id: int) -> dict | None:
    with session(path) as db:
        row = db.get(Task, task_id)
        if row is None:
            return None
        task = as_dict(row)
    # Whether this can be worked on at all, and if not, why. A task page that shows a
    # status of "todo" while the roadmap is quietly holding it back is telling you something
    # untrue about the most important thing on the page.
    held_by: list[str] = []
    milestone_title = None
    if task.get("milestone_id"):
        from kith.infra.db.repositories.projects import roadmap

        graph = roadmap(path, int(task["project_id"] or 0))
        node = next((one for one in graph["milestones"] if one["id"] == task["milestone_id"]), None)
        if node:
            milestone_title = node["title"]
            if node["status"] != "done" and not node["ready"]:
                held_by = list(node["blocked_by"])
    return {
        **task,
        "milestone_title": milestone_title,
        "held_by": held_by,
        "comments": list_task_comments(path, task_id),
        "checklist": list_checklist(path, task_id),
        "deliverables": list_deliverables(path, task_id),
    }


def tasks_awaiting_kith(path: Path) -> list[dict]:
    """Tasks whose newest comment is from the person — i.e. he's been answered and
    should pick the task back up. Powers the ask-on-task loop."""
    candidates = active_tasks(path) + [t for t in list_tasks(path) if t["status"] == "waiting"]
    seen: set[int] = set()
    waiting_on_him = []
    for task in candidates:
        # A waiting task appears in both lists; only consider it once.
        if task["id"] in seen:
            continue
        seen.add(task["id"])
        comments = list_task_comments(path, task["id"])
        if comments and comments[-1]["author"] == "user":
            waiting_on_him.append(task)
    return waiting_on_him
