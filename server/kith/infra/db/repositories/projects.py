"""Projects and the milestones that make up their roadmap."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy import delete as sql_delete

from kith.domain.enums import MILESTONE_STATUSES, PROJECT_STATUSES, TASK_ACTIVE
from kith.infra.db.engine import as_dict, changed, session
from kith.infra.db.models import Conversation, Milestone, MilestoneDep, Project, Task
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


def get_milestone(path: Path, milestone_id: int) -> dict | None:
    """One milestone, or None. The sibling of `get_project`, added because a caller that needs a
    milestone's *project* had to list every milestone to find one row."""
    with session(path) as db:
        row = db.get(Milestone, milestone_id)
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
        db.execute(delete(Milestone).where(Milestone.project_id == project_id))
        return changed(db.execute(delete(Project).where(Project.id == project_id))) > 0


# --------------------------------------------------------------------------- #
# Milestones (the roadmap within a project)
# --------------------------------------------------------------------------- #


@notifies("project")
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


@notifies("project")
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


@notifies("project")
def delete_milestone(path: Path, milestone_id: int) -> bool:
    with session(path) as db:
        return changed(db.execute(delete(Milestone).where(Milestone.id == milestone_id))) > 0


def project_overview(path: Path) -> list[dict]:
    """Projects each with their roadmap (milestones) and task counts — for the
    UI, the tools, and his own awareness of where each project stands.

    Three queries and a group-by in Python, rather than correlated subqueries per
    project: the number of projects is small, and this stays readable.
    """
    all_tasks = list_tasks(path)
    out = []
    for project in list_projects(path):
        project_id = project["id"]
        # The graph's nodes rather than the bare rows: every reader of this — the interface,
        # his own tools, his awareness of where a project stands — needs `ready` and
        # `blocked_by`, because the order is not advisory. A listing of titles and statuses
        # leaves you unable to see why something visible is not something that can be done.
        milestones = roadmap(path, project_id)["milestones"]
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


# --------------------------------------------------------------------------- #
# The roadmap as a graph. This is the part that makes a milestone mean something:
# it decides what is available to work on, rather than describing it afterwards.
# --------------------------------------------------------------------------- #


def dependencies(path: Path, project_id: int | None = None) -> list[dict]:
    """Every edge, optionally for one project."""
    with session(path) as db:
        query = select(MilestoneDep)
        if project_id is not None:
            ours = select(Milestone.id).where(Milestone.project_id == project_id)
            query = query.where(MilestoneDep.milestone_id.in_(ours))
        return [
            {"milestone_id": row.milestone_id, "depends_on_id": row.depends_on_id}
            for row in db.scalars(query).all()
        ]


@notifies("project")
def add_dependency(path: Path, milestone_id: int, depends_on_id: int) -> None:
    """Make one milestone wait for another.

    Refuses a cycle, because a cycle is not a slow roadmap — it is a roadmap where nothing
    is ever available, and he would sit doing nothing with no way to see why. Cheaper to
    refuse the edge than to explain the deadlock later.

    Refuses an id that is not a milestone of this project, for a worse reason. This used to
    accept any integer, and he ordered a five-step roadmap by calling ``add_milestone`` with
    ``after: [0]`` — a position, not an id. Four rows were written pointing at milestone 0,
    which does not exist. Every layer downstream then hid it: :func:`roadmap` drops a
    dependency whose target is not a known milestone, so the graph drew no edges at all and
    reported "5 ready to work". He believed he had laid out an order, the interface showed a
    plan with no order in it, and nothing anywhere said the word "0". A refusal here becomes
    a warning on his own tool result, which he can read and correct.
    """
    if milestone_id == depends_on_id:
        raise ValueError("a milestone cannot wait for itself")
    known = {m["id"]: m for m in list_milestones(path)}
    for label, wanted in (("milestone", milestone_id), ("the one it waits for", depends_on_id)):
        if wanted not in known:
            raise ValueError(
                f"{label} {wanted} is not a milestone — pass the numeric id a milestone "
                "already has, not its position in a list"
            )
    if known[milestone_id]["project_id"] != known[depends_on_id]["project_id"]:
        # A roadmap is read per project, so a cross-project edge is stored and then never
        # shown or honoured anywhere: the same silent nothing in a different disguise.
        raise ValueError("both milestones have to be on the same project")
    if _reaches(path, depends_on_id, milestone_id):
        raise ValueError("that would make a loop — the other one already waits for this")
    with session(path) as db:
        exists = db.scalar(
            select(MilestoneDep).where(
                MilestoneDep.milestone_id == milestone_id,
                MilestoneDep.depends_on_id == depends_on_id,
            )
        )
        if exists is None:
            db.add(MilestoneDep(milestone_id=milestone_id, depends_on_id=depends_on_id))


@notifies("project")
def remove_dependency(path: Path, milestone_id: int, depends_on_id: int) -> None:
    with session(path) as db:
        db.execute(
            sql_delete(MilestoneDep).where(
                MilestoneDep.milestone_id == milestone_id,
                MilestoneDep.depends_on_id == depends_on_id,
            )
        )


def _reaches(path: Path, start: int, target: int) -> bool:
    """Can `target` be reached from `start` by following dependencies?

    Breadth-first with a seen set, so an already-corrupt graph cannot hang the check that
    exists to prevent corruption.
    """
    edges = dependencies(path)
    outgoing: dict[int, list[int]] = {}
    for edge in edges:
        outgoing.setdefault(edge["milestone_id"], []).append(edge["depends_on_id"])
    seen, queue = set(), [start]
    while queue:
        node = queue.pop()
        if node == target:
            return True
        if node in seen:
            continue
        seen.add(node)
        queue.extend(outgoing.get(node, []))
    return False


@notifies("project")
def set_milestone_position(path: Path, milestone_id: int, x: float, y: float) -> None:
    """Remember where someone dragged a node to."""
    with session(path) as db:
        row = db.scalar(select(Milestone).where(Milestone.id == milestone_id))
        if row is not None:
            row.x, row.y = float(x), float(y)


def roadmap(path: Path, project_id: int) -> dict:
    """One project's milestones as a graph, with what is available to work on.

    `ready` is the answer to "what now": not done, and every predecessor done. `blocked`
    carries the reason, so the interface and he himself can say *why* something is waiting
    rather than just showing it greyed out.
    """
    milestones = [m for m in list_milestones(path) if m["project_id"] == project_id]
    edges = dependencies(path, project_id)
    by_id = {m["id"]: m for m in milestones}
    waits_for: dict[int, list[int]] = {m["id"]: [] for m in milestones}
    for edge in edges:
        if edge["milestone_id"] in waits_for:
            waits_for[edge["milestone_id"]].append(edge["depends_on_id"])

    tasks = [t for t in list_tasks(path) if t.get("project_id") == project_id]
    nodes = []
    for milestone in milestones:
        blockers = [
            by_id[dep]["title"]
            for dep in waits_for[milestone["id"]]
            if dep in by_id and by_id[dep]["status"] != "done"
        ]
        mine = [t for t in tasks if t.get("milestone_id") == milestone["id"]]
        nodes.append(
            {
                **milestone,
                "waits_for": waits_for[milestone["id"]],
                "blocked_by": blockers,
                "ready": milestone["status"] != "done" and not blockers,
                "tasks_total": len(mine),
                "tasks_done": sum(1 for t in mine if t["status"] == "done"),
                "tasks_active": sum(1 for t in mine if t["status"] in TASK_ACTIVE),
                # Separately from "active", because this is where he is *right now* — the
                # graph pulses this node, which is what turns a roadmap into something you
                # can watch him walk.
                "tasks_doing": sum(1 for t in mine if t["status"] == "working"),
                "tasks_waiting": sum(1 for t in mine if t["status"] == "waiting"),
            }
        )
    return {"milestones": nodes, "dependencies": edges}


def blocked_milestone_ids(path: Path) -> set[int]:
    """Every milestone that is waiting on an unfinished predecessor, across all projects.

    Used to decide what he may work on, which is why it is one query for everything rather
    than per project: a caller asks this once and then filters its whole task list.
    """
    milestones = list_milestones(path)
    status = {m["id"]: m["status"] for m in milestones}
    waits: dict[int, list[int]] = {}
    for edge in dependencies(path):
        # A predecessor that does not exist is ignored, and that asymmetry is deliberate.
        # This used to read `status.get(dep) != "done"`, which is True for a missing
        # milestone — so a dependency on an id that was never real blocked its milestone
        # forever, since a milestone that does not exist can never become done. Meanwhile
        # roadmap() skips unknown predecessors, so the screen showed the same milestones as
        # ready. He spent five hours with "5 ready to work" on the display and nothing he was
        # allowed to touch, reflecting on the same sentence eight times because the machinery
        # would not hand him the task he kept resolving to do.
        #
        # Bad rows are prevented at the write now (see add_dependency), so this is the second
        # line: if one exists anyway — an older database, a hand-edited row — the failure has
        # to be "he can work" rather than a deadlock nothing on screen can explain.
        if edge["depends_on_id"] in status:
            waits.setdefault(edge["milestone_id"], []).append(edge["depends_on_id"])
    return {
        milestone_id for milestone_id, deps in waits.items() if any(status.get(dep) != "done" for dep in deps)
    }


def milestones_needing_tasks(path: Path) -> list[dict]:
    """Milestones whose turn it is and which have nothing to do under them.

    A project can be laid out and still be inert. Asked for a workout tracker he created the
    project, wrote five milestones, ordered them correctly — and filed no tasks. Everything
    that decides what to do next reads ``active_tasks``, so a roadmap with no tasks under it
    is not "a plan waiting to be broken down", it is indistinguishable from an empty board.
    He went idle, and the project sat there looking finished with 0% done and nothing that
    could ever move it.

    Telling him to file tasks did not work twice, so this is machinery instead: an available
    milestone with no open tasks IS work — the work of breaking it down — and a turn can be
    handed it the same way it is handed a task.

    Only the milestones he could actually act on: an unfinished one, in an active project,
    not waiting on a predecessor. Planning three milestones ahead is how a plan becomes
    fiction, and the one in front of him is the only one he knows enough to break down.
    """
    from kith.infra.db.repositories.tasks import TASK_ACTIVE, list_tasks

    with session(path) as db:
        live = {
            project.id: project.name
            for project in db.scalars(select(Project).where(Project.status == "active")).all()
        }
    if not live:
        return []

    blocked = blocked_milestone_ids(path)
    open_by_milestone: dict[int, int] = {}
    for task in list_tasks(path):
        if task["status"] in TASK_ACTIVE and task.get("milestone_id"):
            open_by_milestone[task["milestone_id"]] = open_by_milestone.get(task["milestone_id"], 0) + 1

    return [
        {
            "id": milestone["id"],
            "title": milestone["title"],
            "project_id": milestone["project_id"],
            "project": live[milestone["project_id"]],
        }
        for milestone in list_milestones(path)
        if milestone["project_id"] in live
        and milestone["status"] != "done"
        and milestone["id"] not in blocked
        and not open_by_milestone.get(milestone["id"])
    ]


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
    it — a list of strings, no joins, no milestones. Only active projects: closing a project
    should take back the freedom that came with linking its folder, otherwise a year of
    finished work leaves a trail of directories he may still write to unasked.
    """
    with session(path) as db:
        rows = db.scalars(select(Project).where(Project.status == "active")).all()
        return [row.directory for row in rows if row.directory]
