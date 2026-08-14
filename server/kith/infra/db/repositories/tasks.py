"""Tasks, and everything hanging off one: the checklist and the deliverables.

The comment thread was the third, and it is gone — 620 rows of "New note on…", every one a
notification, burying the messages that actually wanted an answer. Progress lives in the task's
working file now, questions go through `ask`, and evidence is the checklist and the deliverables.
`task_comments` stays in the schema with its rows: removing a feature is not destroying what was
written with it.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from kith.domain.enums import TASK_ACTIVE, TASK_PRIORITIES, TASK_SETTLED, TASK_STATUSES
from kith.infra.db.engine import as_dict, session
from kith.infra.db.models import ChecklistItem, Deliverable, Milestone, Project, Task
from kith.infra.db.support import notifies, utc_now_iso
from kith.kernel import session_context

# Rank for sorting: high first, then normal, then low.
_PRIORITY_RANK = {"high": 0, "normal": 1, "low": 2}

_DELIVERABLE_KINDS = ("text", "file", "link")


# --------------------------------------------------------------------------- #
# Tasks
# --------------------------------------------------------------------------- #


@notifies("task")
def add_task(
    path: Path,
    goal: str,
    priority: str = "normal",
    description: str = "",
    status: str = "planning",
    created_by: str = "kith",
    project_id: int | None = None,
    milestone_id: int | None = None,
) -> dict:
    if priority not in TASK_PRIORITIES:
        priority = "normal"
    if status not in TASK_STATUSES:
        status = "planning"
    now = utc_now_iso()
    with session(path) as db:
        # A task under a milestone belongs to that milestone's project. Resolved in
        # the same transaction as the insert, so the two can't disagree.
        #
        # And refused if there is no such milestone, which is the hole `set_task_milestone` was
        # closed against and this kept: it looked the milestone up only to copy the project, so a
        # stray id fell through the `if milestone` and was stored anyway. Tasks 91, 92 and 93 on
        # the real board all point at milestone 30, which does not exist — their project is null,
        # the roadmap cannot gate them, and the task page renders the bare number 30 where a
        # title belongs. Nothing said the link was broken.
        #
        # Zero is "no milestone" rather than an error, matching `set_task_milestone`: it is what an
        # empty form field arrives as, and the same value must not be valid on one path and fatal
        # on the other.
        if milestone_id:
            milestone = db.get(Milestone, milestone_id)
            if milestone is None:
                raise ValueError(f"there is no milestone {milestone_id}")
            if not project_id:
                project_id = milestone.project_id
        else:
            milestone_id = None
        row = Task(
            goal=goal,
            status=status,
            priority=priority,
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


@notifies("task")
def set_task_milestone(path: Path, task_id: int, milestone_id: int | None) -> dict | None:
    """Link a task to a milestone, inheriting the milestone's project.

    Refuses an id that is not a milestone. It used to store whatever it was given and only
    look the milestone up to copy the project across, so a stray value — a 0 from a
    `Number("")` in the interface, say — was written straight to the column. The task then
    pointed at a milestone that does not exist: the roadmap could not gate it, the task page
    showed an empty milestone field, and nothing anywhere said the link was broken. That
    happened to a real task, which is why this now validates instead of trusting.

    Zero is treated as "no milestone" rather than rejected, because it is what an empty form
    field arrives as and the intent is unambiguous.
    """
    with session(path) as db:
        row = db.get(Task, task_id)
        if row is None:
            return None
        if not milestone_id:  # None or 0 — no milestone
            row.milestone_id = None
        else:
            milestone = db.get(Milestone, milestone_id)
            if milestone is None:
                raise ValueError(f"there is no milestone {milestone_id}")
            row.milestone_id = milestone_id
            row.project_id = milestone.project_id
        row.updated_at = utc_now_iso()
        db.flush()
        return as_dict(row)


@notifies("task")
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

    * anything not planned/working — nothing to do on it;
    * anything under a done or paused project, so a finished project lets him rest;
    * anything under a milestone whose predecessors are not finished. That last one is why
      milestones exist at all. Before it, the roadmap described progress after the fact
      while raw task priority decided the order — so a "roadmap" imposed nothing and there
      was no reason to keep one. Now a milestone that is waiting keeps its tasks waiting
      with it, and the order someone laid out is the order the work happens in.

    Tasks with no milestone are unaffected: a one-off errand should not need a roadmap.
    """
    return _actionable(path, held_back=False)


def _actionable(path: Path, *, held_back: bool) -> list[dict]:
    """Tasks that could be worked, split by whether their milestone's turn has come.

    One query, because the two callers differed by a single `not` and nothing kept the rest of
    the filter — active status, project not parked — in step between the copies.
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
        and (task.get("milestone_id") in blocked) is held_back
    ]


def waiting_on_the_roadmap(path: Path) -> list[dict]:
    """Actionable tasks held back only because their milestone is waiting.

    Kept separate from ``active_tasks`` so "there is nothing to do" and "there is plenty to
    do but it is not this milestone's turn" are different sentences. He needs to be able to
    say which, and so does the interface — a blocked board that looks identical to an empty
    one is how someone concludes the thing is broken.
    """
    return _actionable(path, held_back=True)


@notifies("task")
def update_task(
    path: Path,
    task_id: int,
    status: str | None = None,
    goal: str | None = None,
    priority: str | None = None,
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
            # Which conversation is on it. Stamped when it starts and cleared the moment it
            # stops being worked, because a stale link is worse than none: it would put a task
            # somebody finished yesterday at the top of the chat you are in now.
            row.conversation_id = _who_is_working() if status == "working" else None
        if goal is not None:
            row.goal = goal
        if priority is not None:
            row.priority = priority
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


def _who_is_working() -> str | None:
    """The conversation this call is happening in, or None outside one.

    Imported here rather than at module scope: this package is storage and that is a service,
    and the cycle is real. None is the honest answer for a reminder firing at four in the
    morning — it is working the task, but there is no session for anyone to watch it in.
    """

    return session_context.current() or None


def _roll_up(db: Session, task: Task) -> None:
    """When a task is finished and it clears its milestone, mark the milestone done.

    Stops there on purpose. This used to keep climbing — clearing a project's last
    milestone marked the project itself done — but closing a project is a claim about
    the whole thing being finished, not a status any amount of task bookkeeping should
    make on a person's behalf. A milestone closing is a fact about the roadmap; a project
    closing is a decision, and it stays theirs even when the board is empty.
    """
    now = utc_now_iso()
    milestone_id = task.milestone_id

    if milestone_id:
        if _open_count(db, Task, Task.milestone_id == milestone_id) == 0:
            milestone = db.get(Milestone, milestone_id)
            if milestone and milestone.status != "done":
                milestone.status = "done"
                milestone.updated_at = now


def _open_count(db: Session, model: type[Task], *conditions) -> int:
    """How many unsettled tasks match — the roll-up's only real question."""
    return int(
        db.scalar(
            select(func.count()).select_from(model).where(*conditions, model.status.not_in(TASK_SETTLED))
        )
        or 0
    )


def _newest_first(task: dict) -> str:
    """Sort key that puts most-recently-updated first (invert the ISO string)."""
    stamp = task.get("updated_at") or task.get("created_at") or ""
    return "".join(chr(255 - ord(character)) for character in stamp)


@notifies("task")
def delete_task(path: Path, task_id: int) -> bool:
    with session(path) as db:
        return db.execute(delete(Task).where(Task.id == task_id)).rowcount > 0


# --------------------------------------------------------------------------- #
# Task detail: comments (two-way), checklist, deliverables
# --------------------------------------------------------------------------- #


@notifies("task")
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


@notifies("task")
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


@notifies("task")
def delete_checklist_item(path: Path, item_id: int) -> bool:
    with session(path) as db:
        return db.execute(delete(ChecklistItem).where(ChecklistItem.id == item_id)).rowcount > 0


@notifies("task")
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


@notifies("task")
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
    # status of "planned" while the roadmap is quietly holding it back is telling you something
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
        "checklist": list_checklist(path, task_id),
        "deliverables": list_deliverables(path, task_id),
        # Read here rather than by each surface, because there are three of them — the drawer,
        # `view_task`, and the approval bar — and the one that forgot was the one you open to
        # read a task. Always present, empty when no plan is filed, so the interface branches on
        # content and not on whether the key arrived.
        "plan": _plan_for(path, task),
    }


def _plan_for(path: Path, task: dict) -> str:
    """The plan for this task, from its project's folder or from his own.

    Not gated on having a project any more. Every task goes through the same
    `backlog → planning → planned` gate now, so requiring a project to *find* a plan made the
    one kind of task that never has one — the standalone errand — the one kind that could not
    carry a plan at all.
    """
    from kith.infra import project_files
    from kith.infra.workspace import paths

    try:
        directory = ""
        if task.get("project_id"):
            from kith.infra.db.repositories.projects import get_project

            project = get_project(path, int(task["project_id"]))
            directory = str((project or {}).get("directory") or "").strip()
        base = Path(directory) if directory and Path(directory).is_dir() else paths.root()
        return project_files.read_plan(base, int(task["id"]))
    except Exception:
        # Same contract as the rest of this: a task must load whether or not its plan does.
        return ""


def working_in(path: Path, conversation_id: str) -> dict | None:
    """The task this conversation is working, with its checklist. None when it is not on one.

    One task at a time by construction: `update_task` clears the link the moment a task stops
    being `working`, so the newest stamp is the only live one. Ordered anyway, because "newest
    wins" is a cheaper thing to be right about than "there is exactly one".
    """
    if not conversation_id:
        return None
    query = (
        select(Task)
        .where(Task.conversation_id == conversation_id, Task.status == "working")
        .order_by(Task.updated_at.desc())
        .limit(1)
    )
    with session(path) as db:
        row = db.scalars(query).first()
        if row is None:
            return None
        task = as_dict(row)
    return {**task, "checklist": list_checklist(path, int(task["id"]))}
