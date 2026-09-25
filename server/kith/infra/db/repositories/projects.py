"""Projects — a name, a folder, and the tasks filed under it."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, select, update

from kith.domain.enums import PROJECT_STATUSES, TASK_ACTIVE, TASK_STATUSES
from kith.infra.db.engine import as_dict, changed, session
from kith.infra.db.models import Conversation, Project, Task
from kith.infra.db.repositories.tasks import list_tasks
from kith.infra.db.support import notifies, utc_now_iso

# --------------------------------------------------------------------------- #
# Projects (group tasks toward a bigger goal)
# --------------------------------------------------------------------------- #


@notifies("project")
def add_project(path: Path, name: str, description: str = "", directory: str | None = None) -> dict:
    now = utc_now_iso()
    with session(path) as db:
        row = Project(
            name=name,
            description=description,
            status="active",
            directory=directory or None,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.flush()
        return as_dict(row)


def list_projects(path: Path) -> list[dict]:
    with session(path) as db:
        rows = db.scalars(select(Project).order_by(Project.updated_at.desc())).all()
        return [as_dict(row) for row in rows]


def get_project(path: Path, project_id: int) -> dict | None:
    with session(path) as db:
        row = db.get(Project, project_id)
        return as_dict(row) if row else None


@notifies("project")
def update_project(
    path: Path,
    project_id: int,
    status: str | None = None,
    name: str | None = None,
    description: str | None = None,
) -> dict | None:
    if status is not None and status not in PROJECT_STATUSES:
        raise ValueError(f"status must be one of {PROJECT_STATUSES}")
    with session(path) as db:
        row = db.get(Project, project_id)
        if row is None:
            return None
        if status is not None:
            row.status = status
        if name is not None:
            row.name = name
        if description is not None:
            row.description = description
        row.updated_at = utc_now_iso()
        db.flush()
        return as_dict(row)


@notifies("project")
def delete_project(path: Path, project_id: int) -> bool:
    with session(path) as db:
        # Orphan the tasks rather than delete them — the work outlives the grouping,
        # and cascading here would silently destroy finished tasks and deliverables.
        db.execute(update(Task).where(Task.project_id == project_id).values(project_id=None))
        # And the conversations, for the same reason and one more. This was missed, so every
        # session bound to a deleted project kept pointing at a row that no longer existed —
        # and a dangling id is not invisible: the history panel groups by it, finds no project
        # to name, and prints the number. Deleting a project left "Project #15" in the sidebar
        # holding one conversation, which is the deleted project still on screen under an id
        # for a name. See ui/components/chat/history-panel.tsx → groupByProject.
        db.execute(update(Conversation).where(Conversation.project_id == project_id).values(project_id=None))
        return changed(db.execute(delete(Project).where(Project.id == project_id))) > 0


def task_counts(tasks: list[dict]) -> dict:
    """How many of these tasks sit in each column, plus the two totals every reader wants.

    ``tasks_active`` keeps its old meaning — approved or working, what he may pick up — because
    his context block and the chat header both read it. ``tasks_total`` counts dropped work too;
    progress is ``done`` over everything that was not dropped.
    """
    out = {f"tasks_{status}": sum(1 for t in tasks if t["status"] == status) for status in TASK_STATUSES}
    out["tasks_active"] = sum(1 for t in tasks if t["status"] in TASK_ACTIVE)
    out["tasks_total"] = len(tasks)
    return out


def project_overview(path: Path) -> list[dict]:
    """Projects, each with how many of its tasks are in each column.

    Two queries and a group-by in Python: the number of projects is small, and this stays
    readable. It used to carry each project's milestone graph as well, which was most of the
    payload and all of the confusion.
    """
    all_tasks = list_tasks(path)
    out = []
    for project in list_projects(path):
        tasks = [t for t in all_tasks if t.get("project_id") == project["id"]]
        out.append({**project, **task_counts(tasks)})
    return out


@notifies("project")
def set_directory(path: Path, project_id: int, directory: str | None) -> dict | None:
    """Point a project at a folder, or unpoint it.

    Separate from ``update_project`` because it is not the same kind of change. Renaming a
    project is cosmetic; giving it a directory grants him a folder on your machine to work in
    unrestricted, which is a decision worth making on its own.
    """
    with session(path) as db:
        row = db.get(Project, project_id)
        if row is None:
            return None
        row.directory = str(directory) if directory else None
        row.updated_at = utc_now_iso()
        db.flush()
        return as_dict(row)


def linked_directories(path: Path) -> list[str]:
    """Every folder an active project is pointed at.

    Read by the permission check, so it is deliberately the narrowest question that answers
    it — a list of strings, no joins. Only active projects: closing a project
    should take back the freedom that came with linking its folder, otherwise a year of
    finished work leaves a trail of directories he may still write to unasked.
    """
    with session(path) as db:
        rows = db.scalars(select(Project).where(Project.status == "active")).all()
        return [row.directory for row in rows if row.directory]
