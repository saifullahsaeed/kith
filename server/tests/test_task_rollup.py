"""Completion cascading: task → milestone → project.

The most intricate logic in the data layer, and the one most likely to be broken by
a well-meaning edit. Its whole job is deciding when work is *finished*, and getting
it wrong is quiet in both directions: a milestone that never closes leaves Kith
poking at a done project forever, and one that closes early makes him rest with
work outstanding.
"""

from __future__ import annotations

from pathlib import Path

from kith.infra.db.repositories import projects, tasks


def test_milestone_waits_for_its_last_task(db: Path) -> None:
    project = projects.add_project(db, "p")
    milestone = projects.add_milestone(db, project["id"], "m")
    first = tasks.add_task(db, "one", milestone_id=milestone["id"])
    second = tasks.add_task(db, "two", milestone_id=milestone["id"])

    tasks.update_task(db, first["id"], status="done")
    assert projects.list_milestones(db, project["id"])[0]["status"] == "todo"

    tasks.update_task(db, second["id"], status="done")
    assert projects.list_milestones(db, project["id"])[0]["status"] == "done"


def test_project_completes_when_its_roadmap_does(db: Path) -> None:
    project = projects.add_project(db, "p")
    milestone = projects.add_milestone(db, project["id"], "m")
    task = tasks.add_task(db, "only", milestone_id=milestone["id"])

    tasks.update_task(db, task["id"], status="done")
    assert projects.get_project(db, project["id"])["status"] == "done"


def test_project_with_no_milestones_completes_on_its_tasks(db: Path) -> None:
    """A project run purely off tasks still finishes — it has no roadmap to clear."""
    project = projects.add_project(db, "p")
    task = tasks.add_task(db, "only", project_id=project["id"])

    tasks.update_task(db, task["id"], status="done")
    assert projects.get_project(db, project["id"])["status"] == "done"


def test_dropped_counts_as_settled(db: Path) -> None:
    """Abandoning a task must not hold its milestone open forever."""
    project = projects.add_project(db, "p")
    milestone = projects.add_milestone(db, project["id"], "m")
    dropped = tasks.add_task(db, "abandon", milestone_id=milestone["id"])
    finished = tasks.add_task(db, "finish", milestone_id=milestone["id"])

    tasks.update_task(db, dropped["id"], status="dropped")
    tasks.update_task(db, finished["id"], status="done")
    assert projects.list_milestones(db, project["id"])[0]["status"] == "done"


def test_paused_project_is_left_alone(db: Path) -> None:
    """Only 'active' rolls to 'done' — a paused project stays as its person left it."""
    project = projects.add_project(db, "p")
    task = tasks.add_task(db, "only", project_id=project["id"])
    projects.update_project(db, project["id"], status="paused")

    tasks.update_task(db, task["id"], status="done")
    assert projects.get_project(db, project["id"])["status"] == "paused"


def test_a_task_inherits_its_milestones_project(db: Path) -> None:
    project = projects.add_project(db, "p")
    milestone = projects.add_milestone(db, project["id"], "m")
    task = tasks.add_task(db, "t", milestone_id=milestone["id"])
    assert task["project_id"] == project["id"]


def test_active_tasks_skip_a_parked_project(db: Path) -> None:
    """A finished or paused project lets him rest — this is what makes that true."""
    project = projects.add_project(db, "p")
    task = tasks.add_task(db, "t", project_id=project["id"])
    assert any(t["id"] == task["id"] for t in tasks.active_tasks(db))

    projects.update_project(db, project["id"], status="paused")
    assert not any(t["id"] == task["id"] for t in tasks.active_tasks(db))


def test_deleting_a_project_keeps_its_tasks(db: Path) -> None:
    """The work outlives the grouping — cascading here would destroy deliverables."""
    project = projects.add_project(db, "p")
    task = tasks.add_task(db, "t", project_id=project["id"])

    projects.delete_project(db, project["id"])
    survivor = tasks.task_detail(db, task["id"])
    assert survivor is not None
    assert survivor["project_id"] is None
