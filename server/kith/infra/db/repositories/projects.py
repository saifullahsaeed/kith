"""Projects and the milestones that make up their roadmap."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, func, select, update

from kith.domain.enums import MILESTONE_STATUSES, PROJECT_STATUSES, TASK_ACTIVE
from kith.infra.db.engine import as_dict, session
from kith.infra.db.models import Milestone, Project, Task
from kith.infra.db.repositories.tasks import list_tasks
from kith.infra.db.support import utc_now_iso

# --------------------------------------------------------------------------- #
# Projects (group tasks toward a bigger goal)
# --------------------------------------------------------------------------- #


def add_project(path: Path, name: str, description: str = "") -> dict:
    now = utc_now_iso()
    with session(path) as db:
        row = Project(name=name, description=description, status="active", created_at=now, updated_at=now)
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


def delete_project(path: Path, project_id: int) -> bool:
    with session(path) as db:
        # Orphan the tasks rather than delete them — the work outlives the grouping,
        # and cascading here would silently destroy finished tasks and deliverables.
        db.execute(update(Task).where(Task.project_id == project_id).values(project_id=None))
        db.execute(delete(Milestone).where(Milestone.project_id == project_id))
        return db.execute(delete(Project).where(Project.id == project_id)).rowcount > 0


# --------------------------------------------------------------------------- #
# Milestones (the roadmap within a project)
# --------------------------------------------------------------------------- #


def add_milestone(path: Path, project_id: int, title: str, target_at: str | None = None) -> dict:
    now = utc_now_iso()
    with session(path) as db:
        # Append to the end of this project's roadmap.
        next_index = db.scalar(
            select(func.coalesce(func.max(Milestone.order_index), -1) + 1).where(
                Milestone.project_id == project_id
            )
        )
        row = Milestone(
            project_id=project_id,
            title=title,
            target_at=target_at,
            status="todo",
            order_index=int(next_index or 0),
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.flush()
        return as_dict(row)


def list_milestones(path: Path, project_id: int | None = None) -> list[dict]:
    query = select(Milestone)
    if project_id is not None:
        query = query.where(Milestone.project_id == project_id).order_by(Milestone.order_index, Milestone.id)
    else:
        query = query.order_by(Milestone.project_id, Milestone.order_index, Milestone.id)
    with session(path) as db:
        return [as_dict(row) for row in db.scalars(query).all()]


def update_milestone(
    path: Path,
    milestone_id: int,
    status: str | None = None,
    title: str | None = None,
    target_at: str | None = None,
) -> dict | None:
    if status is not None and status not in MILESTONE_STATUSES:
        raise ValueError(f"status must be one of {MILESTONE_STATUSES}")
    with session(path) as db:
        row = db.get(Milestone, milestone_id)
        if row is None:
            return None
        if status is not None:
            row.status = status
        if title is not None:
            row.title = title
        if target_at is not None:
            row.target_at = target_at
        row.updated_at = utc_now_iso()
        db.flush()
        return as_dict(row)


def delete_milestone(path: Path, milestone_id: int) -> bool:
    with session(path) as db:
        return db.execute(delete(Milestone).where(Milestone.id == milestone_id)).rowcount > 0


def project_overview(path: Path) -> list[dict]:
    """Projects each with their roadmap (milestones) and task counts — for the
    UI, the tools, and his own awareness of where each project stands.

    Three queries and a group-by in Python, rather than correlated subqueries per
    project: the number of projects is small, and this stays readable.
    """
    all_milestones = list_milestones(path)
    all_tasks = list_tasks(path)
    out = []
    for project in list_projects(path):
        project_id = project["id"]
        milestones = [m for m in all_milestones if m["project_id"] == project_id]
        tasks = [t for t in all_tasks if t.get("project_id") == project_id]
        out.append(
            {
                **project,
                "milestones": milestones,
                "milestones_done": sum(1 for m in milestones if m["status"] == "done"),
                "milestones_total": len(milestones),
                "tasks_active": sum(1 for t in tasks if t["status"] in TASK_ACTIVE),
                "tasks_total": len(tasks),
            }
        )
    return out
