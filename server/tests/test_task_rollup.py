"""What finishing tasks does to a project — which is nothing, deliberately.

This used to be the file for the completion cascade: the last task under a milestone closed the
milestone, and once upon a time the last milestone closed the project. Milestones are gone, and
the project half was removed before them for a better reason than tidiness: a project closing is
a judgement about the whole thing, not a fact bookkeeping can derive from an empty board. It is a
person's call, never Kith's — see test_only_a_person_closes_a_project.py for the tool that would
have let him make it anyway.

What is left is the board summary a project carries, and the rule that decides what he may pick up.
"""

from __future__ import annotations

from pathlib import Path

from kith.infra.db.repositories import projects, tasks


def test_finishing_every_task_does_not_close_the_project(db: Path) -> None:
    """An empty board is evidence the project looks finished, not a decision that it is."""
    project = projects.add_project(db, "p")
    task = tasks.add_task(db, "only", project_id=project["id"])

    tasks.update_task(db, task["id"], status="done")
    assert projects.get_project(db, project["id"])["status"] == "active"


def test_paused_project_is_left_alone(db: Path) -> None:
    project = projects.add_project(db, "p")
    task = tasks.add_task(db, "only", project_id=project["id"])
    projects.update_project(db, project["id"], status="paused")

    tasks.update_task(db, task["id"], status="done")
    assert projects.get_project(db, project["id"])["status"] == "paused"


def test_active_tasks_are_the_approved_and_the_working(db: Path) -> None:
    """Nothing gates an approved task any more but its project being parked. `planning` is not
    pickable — a plan nobody has said yes to is not work yet."""
    project = projects.add_project(db, "p")
    planned = tasks.add_task(db, "a plan", status="planning", project_id=project["id"])
    approved = tasks.add_task(db, "said yes", status="approved", project_id=project["id"])
    working = tasks.add_task(db, "on it", status="working", project_id=project["id"])
    loose = tasks.add_task(db, "an errand", status="approved")

    picked = {t["id"] for t in tasks.active_tasks(db)}
    assert {approved["id"], working["id"], loose["id"]} <= picked
    assert planned["id"] not in picked


def test_active_tasks_come_high_priority_first(db: Path) -> None:
    """With no graph deciding the order, priority does."""
    low = tasks.add_task(db, "later", priority="low", status="approved")
    high = tasks.add_task(db, "first", priority="high", status="approved")
    order = [t["id"] for t in tasks.active_tasks(db)]
    assert order.index(high["id"]) < order.index(low["id"])


def test_active_tasks_skip_a_parked_project(db: Path) -> None:
    """A finished or paused project lets him rest — this is what makes that true."""
    project = projects.add_project(db, "p")
    task = tasks.add_task(db, "t", status="approved", project_id=project["id"])
    assert any(t["id"] == task["id"] for t in tasks.active_tasks(db))

    projects.update_project(db, project["id"], status="paused")
    assert not any(t["id"] == task["id"] for t in tasks.active_tasks(db))


def test_a_project_counts_its_tasks_by_column(db: Path) -> None:
    """The board, summarised — what the projects list and his context both read."""
    project = projects.add_project(db, "p")
    for status in ("planning", "planning", "approved", "working", "done", "dropped"):
        tasks.add_task(db, f"a {status} task", status=status, project_id=project["id"])

    row = next(one for one in projects.project_overview(db) if one["id"] == project["id"])
    assert (row["tasks_planning"], row["tasks_approved"], row["tasks_working"]) == (2, 1, 1)
    assert (row["tasks_done"], row["tasks_dropped"]) == (1, 1)
    assert row["tasks_active"] == 2
    assert row["tasks_total"] == 6
    assert "milestones" not in row


def test_deleting_a_project_keeps_its_tasks(db: Path) -> None:
    """The work outlives the grouping — cascading here would destroy deliverables."""
    project = projects.add_project(db, "p")
    task = tasks.add_task(db, "t", project_id=project["id"])

    projects.delete_project(db, project["id"])
    survivor = tasks.task_detail(db, task["id"])
    assert survivor is not None
    assert survivor["project_id"] is None


def test_a_project_lists_what_its_tasks_produced(db: Path) -> None:
    """The Outputs tab: every deliverable under the project, newest first, with its task."""
    project = projects.add_project(db, "p")
    first = tasks.add_task(db, "write the spec", project_id=project["id"])
    second = tasks.add_task(db, "draw the screens", project_id=project["id"])
    elsewhere = tasks.add_task(db, "someone else's")
    tasks.add_deliverable(db, first["id"], "file", "Spec", "docs/spec.md")
    tasks.add_deliverable(db, second["id"], "link", "Board", "https://example.com")
    tasks.add_deliverable(db, elsewhere["id"], "text", "Not ours", "…")

    out = tasks.project_deliverables(db, project["id"])
    assert [one["title"] for one in out] == ["Board", "Spec"]
    assert out[1]["task_goal"] == "write the spec"
    assert tasks.deliverable_counts(db)[first["id"]] == 1
