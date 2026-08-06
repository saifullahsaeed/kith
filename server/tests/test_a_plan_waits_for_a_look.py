"""A drafted plan is shown before any implementation starts on it.

`planning` is entered only from chat, by a person asking for a plan — never a tick's own
initiative, the same way `waiting` and `review` are never a tick's to choose either. Once a
plan is drafted, `_awaiting_approval` is how it becomes visible enough to actually get looked
at: the same shape `_awaiting_review` already uses for finished-but-unchecked work, because a
queue nobody is shown is a queue nobody works.
"""

from __future__ import annotations

import sys

import pytest

from kith.domain.enums import TASK_ACTIVE, TASK_SETTLED, TASK_STATUSES
from kith.infra.db import repositories as repo
from kith.tools import run_tool


def runner_module():
    return sys.modules["kith.autonomy.runner"]


@pytest.fixture
def runner(db, monkeypatch):
    module = runner_module()
    monkeypatch.setattr(module, "AGENT_DB_PATH", db)
    return module.AutonomyRunner()


class TestTheVocabulary:
    def test_the_eight_columns_and_no_others(self):
        assert TASK_STATUSES == (
            "backlog",
            "planning",
            "planned",
            "working",
            "review",
            "waiting",
            "done",
            "dropped",
        )

    def test_planning_is_not_active(self):
        """Drafting a plan is not doing the work — a tick must not be handed a task still
        waiting on its plan to be looked at."""
        assert "planning" not in TASK_ACTIVE

    def test_backlog_review_and_waiting_are_not_active_either(self):
        for status in ("backlog", "review", "waiting"):
            assert status not in TASK_ACTIVE

    def test_settled_and_active_do_not_overlap(self):
        assert set(TASK_ACTIVE) & set(TASK_SETTLED) == set()


class TestAwaitingApproval:
    def test_a_plan_in_planning_is_listed(self, db, runner):
        task = repo.tasks.add_task(db, "Build the thing", "normal", None, "", "planning", "kith")

        found = runner._awaiting_approval()

        assert [f["id"] for f in found] == [task["id"]]
        assert found[0]["goal"] == "Build the thing"

    def test_other_columns_are_not_listed(self, db, runner):
        for status in ("backlog", "planned", "working", "review", "waiting", "done", "dropped"):
            repo.tasks.add_task(db, f"a {status} task", "normal", None, "", status, "kith")

        assert runner._awaiting_approval() == []

    def test_capped_at_a_handful(self, db, runner):
        for n in range(10):
            repo.tasks.add_task(db, f"plan {n}", "normal", None, "", "planning", "kith")

        assert len(runner._awaiting_approval()) <= 6

    def test_a_read_failure_is_silent(self, db, runner, monkeypatch):
        """A queue that cannot be read is not a reason to fail the whole status endpoint."""
        monkeypatch.setattr(
            runner_module().repo.tasks, "list_tasks", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError())
        )
        assert runner._awaiting_approval() == []

    def test_it_is_in_the_status_payload(self, db, runner):
        repo.tasks.add_task(db, "Build the thing", "normal", None, "", "planning", "kith")

        assert [t["id"] for t in runner.status()["toApprove"]] == [
            t["id"] for t in runner._awaiting_approval()
        ]


class TestThePlanTravelsWithTheApproval:
    """Moving a task to `planning` is also the moment someone has to actually read the plan —
    so the doc the skill wrote comes along with the status change, not just its filename."""

    def test_the_doc_at_the_convention_is_attached(self, db, tmp_path):
        project = repo.projects.add_project(db, "App", "", str(tmp_path))
        task = repo.tasks.add_task(db, "Ship it", "normal", None, "", "backlog", "kith", int(project["id"]))
        work = tmp_path / ".kith" / "work"
        work.mkdir(parents=True)
        (work / f"task-{task['id']}.md").write_text("# The plan\n\nDo the thing carefully.\n")

        out = run_tool("update_task", {"id": task["id"], "status": "planning"}, db)["result"]

        assert "Do the thing carefully" in out["plan"]

    def test_no_project_directory_is_silent(self, db):
        task = repo.tasks.add_task(db, "Ship it", "normal", None, "", "backlog", "kith")

        out = run_tool("update_task", {"id": task["id"], "status": "planning"}, db)["result"]

        assert "plan" not in out

    def test_no_file_at_the_convention_is_silent(self, db, tmp_path):
        project = repo.projects.add_project(db, "App", "", str(tmp_path))
        task = repo.tasks.add_task(db, "Ship it", "normal", None, "", "backlog", "kith", int(project["id"]))

        out = run_tool("update_task", {"id": task["id"], "status": "planning"}, db)["result"]

        assert "plan" not in out

    def test_other_transitions_never_attach_it(self, db, tmp_path):
        """The file might genuinely exist from an earlier round — it is only worth surfacing
        again at the moment someone is actually being asked to approve it."""
        project = repo.projects.add_project(db, "App", "", str(tmp_path))
        task = repo.tasks.add_task(db, "Ship it", "normal", None, "", "backlog", "kith", int(project["id"]))
        work = tmp_path / ".kith" / "work"
        work.mkdir(parents=True)
        (work / f"task-{task['id']}.md").write_text("# The plan\n")

        out = run_tool("update_task", {"id": task["id"], "status": "working"}, db)["result"]

        assert "plan" not in out
