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

import sys

import pytest

from kith.infra.db import repositories as repo


def runner_module():
    return sys.modules["kith.autonomy.runner"]


@pytest.fixture
def finished_project(db):
    project = repo.projects.add_project(db, "Client Portal", "a portal")
    project_id = int(project["id"])
    repo.projects.update_project(db, project_id, status="done")
    return project_id


class TestFilingWorkIntoAFinishedProject:
    def test_adding_an_actionable_task_reopens_it(self, db, finished_project):
        """The task is the evidence. Someone deciding there is more to do is a fact about the
        project, not a mistake to correct."""
        from kith.tools import registry

        registry.get("add_task").run(
            db,
            {
                "goal": "Build the dashboard shell",
                "description": "npm run build passes and /dashboard renders when logged in",
                "project_id": finished_project,
                "status": "planned",
            },
        )

        assert repo.projects.get_project(db, finished_project)["status"] == "active"

    def test_and_says_that_it_did(self, db, finished_project):
        from kith.tools import registry

        made = registry.get("add_task").run(
            db,
            {
                "goal": "Build the dashboard shell",
                "description": "npm run build passes and /dashboard renders when logged in",
                "project_id": finished_project,
                "status": "planned",
            },
        )

        assert "active" in (made.get("note") or "")

    def test_promoting_a_backlog_task_reopens_it_too(self, db, finished_project):
        from kith.tools import registry

        task = repo.tasks.add_task(db, "Later", "normal", None, "", "backlog", "kith", finished_project)
        repo.projects.update_project(db, finished_project, status="done")

        registry.get("update_task").run(db, {"id": int(task["id"]), "status": "planned"})

        assert repo.projects.get_project(db, finished_project)["status"] == "active"

    def test_a_backlog_task_does_not_reopen_anything(self, db, finished_project):
        """Filing something for later is not saying the project is unfinished."""
        from kith.tools import registry

        registry.get("add_task").run(
            db,
            {
                "goal": "Someday",
                "description": "a thing to consider much later, with a real finish line here",
                "project_id": finished_project,
                "status": "backlog",
            },
        )

        assert repo.projects.get_project(db, finished_project)["status"] == "done"

    def test_the_task_becomes_pickable(self, db, finished_project):
        """The point of all of it."""
        from kith.tools import registry

        registry.get("add_task").run(
            db,
            {
                "goal": "Build the dashboard shell",
                "description": "npm run build passes and /dashboard renders when logged in",
                "project_id": finished_project,
                "status": "planned",
            },
        )

        assert [t["goal"] for t in repo.tasks.active_tasks(db)] == ["Build the dashboard shell"]


class TestSayingWhyHeIsIdle:
    def test_a_closed_project_holding_work_is_named(self, db, monkeypatch, finished_project):
        """ "Caught up — resting" with two todo tasks on the board is a lie by omission, and
        it is the reason this took so long to find."""
        monkeypatch.setattr(runner_module(), "AGENT_DB_PATH", db)
        repo.tasks.add_task(db, "Dashboard", "high", None, "", "planned", "kith", finished_project)
        repo.tasks.add_task(db, "Design system", "high", None, "", "planned", "kith", finished_project)

        status, note = runner_module().AutonomyRunner()._why_idle()

        assert "closed project" in status
        assert "Client Portal" in note
        assert "Reopen" in note

    def test_a_genuinely_empty_board_still_says_caught_up(self, db, monkeypatch):
        monkeypatch.setattr(runner_module(), "AGENT_DB_PATH", db)
        status, note = runner_module().AutonomyRunner()._why_idle()
        assert status == "caught up — resting"
        assert note == ""

    def test_an_active_project_with_work_is_not_reported_as_shut_out(self, db, monkeypatch):
        """It would be picked up, so there is nothing to explain."""
        monkeypatch.setattr(runner_module(), "AGENT_DB_PATH", db)
        project = repo.projects.add_project(db, "Live", "ongoing")
        repo.tasks.add_task(db, "Do it", "high", None, "", "planned", "kith", int(project["id"]))

        assert runner_module().AutonomyRunner()._shut_out(None, set()) == []

    def test_a_paused_project_counts_as_closed(self, db, monkeypatch):
        monkeypatch.setattr(runner_module(), "AGENT_DB_PATH", db)
        project = repo.projects.add_project(db, "On hold", "later")
        repo.projects.update_project(db, int(project["id"]), status="paused")
        repo.tasks.add_task(db, "Do it", "high", None, "", "planned", "kith", int(project["id"]))

        shut = runner_module().AutonomyRunner()._shut_out(None, set())

        assert len(shut) == 1 and shut[0]["project"] == "On hold"

    def test_a_backlog_task_is_not_shut_out(self, db, monkeypatch, finished_project):
        """It would not run in an open project either, so the project is not the reason."""
        monkeypatch.setattr(runner_module(), "AGENT_DB_PATH", db)
        repo.tasks.add_task(db, "Later", "normal", None, "", "backlog", "kith", finished_project)

        assert runner_module().AutonomyRunner()._shut_out(None, set()) == []
