"""Tasks, and everything hanging off one: the checklist and the deliverables.

The comment thread was the third, and it is gone — 620 rows of "New note on…", every one a
notification, burying the messages that actually wanted an answer. Progress lives in the task's
working file now, questions go through `ask`, and evidence is the checklist and the deliverables.
`task_comments` stays in the schema with its rows: removing a feature is not destroying what was
written with it.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, func, or_, select

from kith.domain import keys
from kith.domain.enums import TASK_ACTIVE, TASK_PRIORITIES, TASK_STATUSES
from kith.infra import identity, project_files
from kith.infra.db.engine import as_dict, changed, session
from kith.infra.db.models import ChecklistItem, Deliverable, Project, Task
from kith.infra.db.support import created_before, notifies, span_of, utc_now_iso
from kith.kernel import session_context

# Rank for sorting: high first, then normal, then low.
_PRIORITY_RANK = {"high": 0, "normal": 1, "low": 2}

_DELIVERABLE_KINDS = ("text", "file", "link")


# --------------------------------------------------------------------------- #
# Tasks
# --------------------------------------------------------------------------- #


@notifies("task")
def _project_dir(db, project_id: int | None) -> str:
    """Where this task's project lives, for reading the git identity there. "" for a task that
    belongs to no folder — then the machine's global identity is the honest answer."""
    if not project_id:
        return ""
    row = db.get(Project, int(project_id))
    return str(getattr(row, "directory", "") or "")


def add_task(
    path: Path,
    goal: str,
    priority: str = "normal",
    description: str = "",
    status: str = "planning",
    created_by: str = "kith",
    project_id: int | None = None,
) -> dict:
    if priority not in TASK_PRIORITIES:
        priority = "normal"
    if status not in TASK_STATUSES:
        status = "planning"
    now = utc_now_iso()
    with session(path) as db:
        row = Task(
            goal=goal,
            status=status,
            priority=priority,
            description=description,
            created_by=created_by,
            # Stamped where the task is written rather than passed down from the tool, so a
            # caller cannot forget it — and read from the project's own repository, because a
            # work identity and a personal one are a real distinction people already keep in
            # `git config` and the task should land under whichever one will sign its commit.
            account=identity.whoami(_project_dir(db, project_id)),
            key=keys.new_key(),
            project_id=project_id,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
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
    tasks.sort(key=lambda task: (_PRIORITY_RANK.get(str(task.get("priority") or ""), 1), _newest_first(task)))
    return tasks


def tasks_before(path: Path, before: str | None, limit: int) -> list[dict]:
    """A page of the lifetime's tasks, by when they were filed — see `support.created_before`.

    Not :func:`list_tasks`: that is the board's order (priority first), and the lifetime's is
    time, which is the order the LIMIT has to cut in.
    """
    with session(path) as db:
        return [as_dict(row) for row in db.scalars(created_before(Task, before, limit)).all()]


def tasks_span(path: Path) -> tuple[int, str | None]:
    """How many there are, and when the first was written — see `support.span_of`."""
    with session(path) as db:
        count, first = db.execute(span_of(Task)).one()
        return int(count or 0), first


def active_tasks(path: Path) -> list[dict]:
    """What he should actually work next, highest priority first.

    Two things are excluded:

    * anything not approved/working — nothing to do on it;
    * anything under a done or paused project, so a finished project lets him rest.

    There used to be a third: anything under a milestone whose predecessors were not finished.
    Milestones are gone, and that gate went with them. It was the one that sat him idle for five
    hours with "5 ready to work" on screen, and the one a stray ``after: [0]`` could turn into a
    deadlock nothing displayed. Priority and the approval gate decide the order now; a person
    who wants B after A approves A first.
    """
    with session(path) as db:
        parked = set(db.scalars(select(Project.id).where(Project.status != "active")).all())
    return [
        task
        for task in list_tasks(path)
        if task["status"] in TASK_ACTIVE and task.get("project_id") not in parked
    ]


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
        return as_dict(row)


def _who_is_working() -> str | None:
    """The conversation this call is happening in, or None outside one.

    Imported here rather than at module scope: this package is storage and that is a service,
    and the cycle is real. None is the honest answer for a reminder firing at four in the
    morning — it is working the task, but there is no session for anyone to watch it in.
    """

    return session_context.current() or None


def _newest_first(task: dict) -> str:
    """Sort key that puts most-recently-updated first (invert the ISO string)."""
    stamp = task.get("updated_at") or task.get("created_at") or ""
    return "".join(chr(255 - ord(character)) for character in stamp)


@notifies("task")
@notifies("task")
def set_origin(path: Path, task_id: int, key: str = "", account: str = "") -> None:
    """Stamp where a task came from, when it did not come from here.

    `add_task` stamps both for work that starts on this machine. Work imported from a project's
    folder started on somebody else's and already has a name: overwriting after the insert keeps
    `add_task` honest about what it does rather than giving it an "unless" — and the key has to
    survive, because it is the only thing that makes it the same task on both machines.
    """
    with session(path) as db:
        row = db.get(Task, int(task_id))
        if row is None:
            return
        if key:
            row.key = key
        if account:
            row.account = account


def delete_task(path: Path, task_id: int) -> bool:
    """Delete a task, and the brief that describes it in its project.

    The brief was written by `_mirror_brief` on every change and removed by nothing, which made
    the folder append-only: measured on a real project, 17 of its 57 briefs named tasks that had
    been gone for weeks. Harmless while the database is the board and the reverse of harmless
    once the files are — a brief nobody deletes is a task nobody can close.

    The folder is read *before* the row goes, because the row is how the folder is found.
    """
    with session(path) as db:
        row = db.get(Task, int(task_id))
        directory = _project_dir(db, getattr(row, "project_id", None)) if row else ""
        deleted = changed(db.execute(delete(Task).where(Task.id == task_id))) > 0
    if deleted and directory:
        # Silent, like the write it undoes: the task is gone either way, and a brief that could
        # not be removed is worth less than a deletion that failed because of it.
        project_files.forget_brief(directory, int(task_id), str(getattr(row, "key", "") or ""))
    return deleted


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


def checklist_progress(path: Path, task_ids: list[int]) -> dict[int, tuple[int, int]]:
    """How far through its checklist each of these tasks is, as {task_id: (done, total)}.

    One query for the whole set rather than `list_checklist` per task. The caller is the system
    prompt, which asks about every task in flight on a project on every turn — a query each is
    the shape that is fine with three tasks and quietly is not with thirty.

    Tasks with no checklist are absent from the mapping rather than present as ``(0, 0)``: "no
    checklist" and "a checklist nothing is ticked on" mean different things, and the second is
    the one worth a line.
    """
    if not task_ids:
        return {}
    query = select(ChecklistItem.task_id, ChecklistItem.done).where(ChecklistItem.task_id.in_(task_ids))
    out: dict[int, tuple[int, int]] = {}
    with session(path) as db:
        for task_id, done in db.execute(query).all():
            was = out.get(int(task_id), (0, 0))
            out[int(task_id)] = (was[0] + (1 if done else 0), was[1] + 1)
    return out


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
        return changed(db.execute(delete(ChecklistItem).where(ChecklistItem.id == item_id))) > 0


@notifies("task")
def add_deliverable(path: Path, task_id: int, kind: str, title: str, content: str) -> dict:
    """The output a task produced, carrying the project it belongs to.

    Denormalised from the task deliberately. A deliverable's project used to be knowable only by
    joining back — so anything holding a deliverable and nothing else could not anchor a
    workspace path, which is exactly what the design board hit: it had a task id, needed a
    project to write its file against, and had to block on a second request to learn one.
    """
    if kind not in _DELIVERABLE_KINDS:
        kind = "text"
    with session(path) as db:
        owner = db.scalar(select(Task.project_id).where(Task.id == task_id))
        row = Deliverable(
            task_id=task_id,
            kind=kind,
            title=title,
            content=content,
            created_at=utc_now_iso(),
            project_id=owner if owner is not None else session_context.current_project(),
        )
        db.add(row)
        db.flush()
        return as_dict(row)


def list_deliverables(path: Path, task_id: int) -> list[dict]:
    query = select(Deliverable).where(Deliverable.task_id == task_id).order_by(Deliverable.id)
    with session(path) as db:
        return [as_dict(row) for row in db.scalars(query).all()]


def deliverable_counts(path: Path) -> dict[int, int]:
    """How many deliverables each task has, as {task_id: n} — one grouped query for a board."""
    query = select(Deliverable.task_id, func.count()).group_by(Deliverable.task_id)
    with session(path) as db:
        return {int(task_id): int(n) for task_id, n in db.execute(query).all()}


def project_deliverables(path: Path, project_id: int) -> list[dict]:
    """Everything a project has produced, newest first, each with the goal of the task behind it.

    By the column *or* by the task, because the column is younger than most rows: a deliverable
    filed before v47 carries no project of its own and is only the project's through its task.
    """
    owned = select(Task.id).where(Task.project_id == project_id)
    query = (
        select(Deliverable, Task.goal)
        .join(Task, Task.id == Deliverable.task_id, isouter=True)
        .where(or_(Deliverable.project_id == project_id, Deliverable.task_id.in_(owned)))
        .order_by(Deliverable.created_at.desc(), Deliverable.id.desc())
    )
    with session(path) as db:
        return [{**as_dict(row), "task_goal": goal or ""} for row, goal in db.execute(query).all()]


@notifies("task")
def delete_deliverable(path: Path, deliverable_id: int) -> bool:
    with session(path) as db:
        return changed(db.execute(delete(Deliverable).where(Deliverable.id == deliverable_id))) > 0


def task_detail(path: Path, task_id: int) -> dict | None:
    with session(path) as db:
        row = db.get(Task, task_id)
        if row is None:
            return None
        task = as_dict(row)
    return {
        **task,
        "checklist": list_checklist(path, task_id),
        "deliverables": list_deliverables(path, task_id),
        # Read here rather than by each surface, because there are three of them — the drawer,
        # `view_task`, and the approval bar — and the one that forgot was the one you open to
        # read a task. Always present, empty when no plan is filed, so the interface branches on
        # content and not on whether the key arrived.
        "plan": _plan_for(path, task),
        # When the plan file last changed, so the task page can say "written 17 Sep" and put the
        # plan in its activity at the time it was written rather than when the task was filed.
        "plan_modified": _plan_modified(path, task),
    }


def _plan_for(path: Path, task: dict) -> str:
    """The plan for this task, from its project's folder or from his own.

    Not gated on having a project any more. Every task goes through the same
    `backlog → planning → planned` gate now, so requiring a project to *find* a plan made the
    one kind of task that never has one — the standalone errand — the one kind that could not
    carry a plan at all.
    """
    from kith.infra import project_files

    try:
        return project_files.read_plan(_plan_base(path, task), int(task["id"]))
    except Exception:
        # Same contract as the rest of this: a task must load whether or not its plan does.
        return ""


def _plan_base(path: Path, task: dict) -> Path:
    """The folder a task's plan lives under: its project's, or his own."""
    from kith.infra.workspace import paths

    directory = ""
    if task.get("project_id"):
        from kith.infra.db.repositories.projects import get_project

        project = get_project(path, int(task["project_id"]))
        directory = str((project or {}).get("directory") or "").strip()
    return Path(directory) if directory and Path(directory).is_dir() else paths.root()


def _plan_modified(path: Path, task: dict) -> str:
    """The plan file's modification time as a UTC ISO string, or "" when there is no plan."""
    from datetime import UTC, datetime

    from kith.infra import project_files

    try:
        file = project_files.plan_path(_plan_base(path, task), int(task["id"]))
        return datetime.fromtimestamp(file.stat().st_mtime, UTC).isoformat() if file.is_file() else ""
    except Exception:
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
