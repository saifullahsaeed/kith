"""Work in a finished project is not silently unreachable.

A person can close a project at any point — including one with an open board — and nothing then
stopped an actionable task being added underneath afterward, where `active_tasks` would never
return it — so the board showed two `todo` tasks, the person pressed Run, and every tick answered
"caught up — resting". Three symptoms, one cause, and none of them pointed at it:

* the work never started, and the reason given was the most reassuring sentence available;
* the project picker showed a bare `1` instead of a name, because the interface lists only
  active projects and had nothing to match the id against;
* the loop woke on each new task, found nothing it was allowed to touch, and slept again —
  visible in the feed as working / caught up / working / caught up.
"""

from __future__ import annotations

import pytest

from kith.infra.db import repositories as repo


@pytest.fixture
def finished_project(db, tmp_path):
    """A closed project with a real folder, so a task under it can carry a plan.

    The folder matters now: approval requires a plan at `.kith/work/task-<id>.md`, resolved against
    the project's directory, so a project without one cannot have approvable work.
    """
    project = repo.projects.add_project(db, "Client Portal", "a portal", str(tmp_path))
    project_id = int(project["id"])
    repo.projects.update_project(db, project_id, status="done")
    return project_id


def _approvable(db, project_id, tmp_path, goal="Build the dashboard shell"):
    """A task under the project with everything approval requires, still in `planning`."""
    task = repo.tasks.add_task(
        db,
        goal,
        "normal",
        "npm run build passes and /dashboard renders when logged in",
        "planning",
        "kith",
        project_id,
    )
    work = tmp_path / ".kith" / "work"
    work.mkdir(parents=True, exist_ok=True)
    (work / f"task-{task['id']}.md").write_text("# Plan\n\nBuild it.\n")
    repo.tasks.add_checklist_item(db, int(task["id"]), "Build the shell")
    return task


class TestFilingWorkIntoAFinishedProject:
    """Approving work is what reopens it, rather than filing work.

    This used to fire when an *actionable* task was added, because a task could be created straight
    into a pickable column. Nothing can any more — everything starts in `planning` and only an
    approved plan is pickable — so the trigger moved to the moment work actually becomes actionable.
    That is a better place for it than the old one: filing a task under a finished project is not a
    claim that the project is live, and approving one is exactly that claim.
    """

    def test_approving_work_reopens_it(self, db, finished_project, tmp_path):
        """The task is the evidence. Someone deciding there is more to do is a fact about the
        project, not a mistake to correct."""
        from kith.tools import registry

        task = _approvable(db, finished_project, tmp_path)
        registry.require("update_task").run(db, {"id": int(task["id"]), "status": "approved"})

        assert repo.projects.get_project(db, finished_project)["status"] == "active"

    def test_and_says_that_it_did(self, db, finished_project, tmp_path):
        from kith.tools import registry

        task = _approvable(db, finished_project, tmp_path)
        out = registry.require("update_task").run(db, {"id": int(task["id"]), "status": "approved"})

        assert "active" in (out.get("note") or "")

    def test_the_task_becomes_pickable(self, db, finished_project, tmp_path):
        """The point of all of it."""
        from kith.tools import registry

        task = _approvable(db, finished_project, tmp_path)
        registry.require("update_task").run(db, {"id": int(task["id"]), "status": "approved"})

        assert [t["goal"] for t in repo.tasks.active_tasks(db)] == ["Build the dashboard shell"]

    def test_filing_something_for_later_does_not_reopen_anything(self, db, finished_project):
        """Filing something for later is not saying the project is unfinished."""
        from kith.tools import registry

        registry.require("add_task").run(
            db,
            {
                "goal": "Someday",
                "description": "a thing to consider much later, with a real finish line here",
                "project_id": finished_project,
                "status": "planning",
            },
        )

        assert repo.projects.get_project(db, finished_project)["status"] == "done"
