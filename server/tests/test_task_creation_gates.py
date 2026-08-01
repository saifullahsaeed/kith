"""Task creation is gated at the one chokepoint — the add_task tool handler.

Three gates so a bad plan can't start: real tasks need a checkable done-condition (kills
"Verify the Prisma foundation"), near-identical open tasks merge instead of piling up (kills
the 106≈110 duplicates), and one milestone can't hold the whole roadmap at once.
"""

from kith.infra.db import repositories as repo
from kith.services import tuning
from kith.tools import tasks as task_tools


def test_scoped_task_without_a_done_condition_is_refused(db):
    res = task_tools.add_task(db, {"goal": "Verify the Prisma foundation", "project_id": 1})
    assert res.get("ok") is False
    assert "done" in (res.get("error") or "").lower()


def test_scoped_task_with_a_done_condition_is_created(db):
    res = task_tools.add_task(
        db,
        {
            "goal": "Make db:generate pass",
            "description": "Done when npm run db:generate exits 0",
            "project_id": 1,
        },
    )
    assert res.get("id")


def test_trivial_standalone_task_needs_no_done_condition(db):
    res = task_tools.add_task(db, {"goal": "write ALPHA to alpha.txt"})
    assert res.get("id")


def test_a_near_identical_open_task_is_merged(db):
    first = task_tools.add_task(
        db,
        {
            "goal": "Set up the Prisma database schema",
            "description": "done when schema.prisma has the six models",
            "project_id": 7,
        },
    )
    dup = task_tools.add_task(
        db,
        {
            "goal": "Set up the Prisma database schema properly",
            "description": "done when the Prisma schema is built",
            "project_id": 7,
        },
    )
    assert dup.get("duplicate") is True
    assert dup["id"] == first["id"]
    ids = [t["id"] for t in repo.tasks.list_tasks(db)]
    assert ids.count(first["id"]) == 1  # the duplicate was merged, not created


def test_milestone_breakdown_is_capped(db):
    tuning.apply({"milestone_task_cap": 3})
    proj = repo.projects.add_project(db, "Warehouse")
    ms = repo.projects.add_milestone(db, proj["id"], "Data foundation")
    for goal in ("Configure the database layer", "Write the seed script", "Add receiving endpoint"):
        res = task_tools.add_task(
            db,
            {"goal": goal, "description": f"done when {goal.lower()} works", "milestone_id": ms["id"]},
        )
        assert res.get("id"), f"{goal} should have been created"

    over = task_tools.add_task(
        db,
        {
            "goal": "Build the shipping module",
            "description": "done when shipping works end to end",
            "milestone_id": ms["id"],
        },
    )
    assert over.get("ok") is False
    assert "milestone" in (over.get("error") or "").lower()
