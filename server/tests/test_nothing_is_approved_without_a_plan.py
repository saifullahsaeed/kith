"""Approval means a plan *and* the checklist, or it is not approval.

The one decision left to a person about a task is saying yes to its plan. That decision is only
worth anything if the thing being approved is complete, and it was not: on the first real test of
the new vocabulary he was asked to plan a task, wrote a good 1,449-character plan, attached it —
and created no checklist items at all. Nothing was wrong with what he did. The `planning-a-task`
skill describes the plan document in detail and never mentions the checklist (its only two
references to one are *warnings* against writing it early), and `update_task(status='approved')`
with zero items returned `ok: True`.

So the gate lives here rather than in the skill. Prose can be forgotten, misread, or skipped when
a task looks small; a refusal cannot. And the refusal has to say *which* of the two is missing,
because the caller is a model that will otherwise retry the identical call.

The checklist is the part worth insisting on. A plan is prose and can describe anything; the
checklist is what makes progress legible afterwards — it is what the working-task card counts
through, and a task approved without one shows a person nothing between "started" and "claims to
be finished".
"""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo
from kith.infra.workspace import paths
from kith.tools import run_tool

BRIEF = "Done when workflow-test.md exists and names the milestone it belongs to."


def _plan_for(directory: Path, task_id: int) -> None:
    work = directory / ".kith" / "work"
    work.mkdir(parents=True, exist_ok=True)
    (work / f"task-{task_id}.md").write_text("# Plan\n\nRead the thing, then write the thing.\n")


def _a_task(db: Path, directory: Path) -> dict:
    project = repo.projects.add_project(db, "Project Management Test", "", str(directory))
    return repo.tasks.add_task(
        db, "Make the artifact", "normal", BRIEF, "planning", "kith", int(project["id"])
    )


def _approve(db: Path, task_id: int) -> dict:
    return run_tool("update_task", {"id": task_id, "status": "approved"}, db)


def _status(db: Path, task_id: int) -> str:
    return str((repo.tasks.task_detail(db, task_id) or {}).get("status") or "")


class TestApprovingWithoutTheParts:
    def test_a_plan_with_no_checklist_is_refused(self, db: Path, tmp_path: Path):
        task = _a_task(db, tmp_path)
        _plan_for(tmp_path, task["id"])

        out = _approve(db, task["id"])

        assert "blocked" in (out.get("result") or {}), out
        assert _status(db, task["id"]) == "planning"

    def test_and_says_it_is_the_checklist_that_is_missing(self, db: Path, tmp_path: Path):
        task = _a_task(db, tmp_path)
        _plan_for(tmp_path, task["id"])

        out = _approve(db, task["id"])

        said = str(out.get("result") or {}).lower()
        assert "checklist" in said
        # And not the other one, or the message sends him to fix something that is already right.
        assert "no plan" not in said

    def test_a_checklist_with_no_plan_is_refused(self, db: Path, tmp_path: Path):
        task = _a_task(db, tmp_path)
        repo.tasks.add_checklist_item(db, int(task["id"]), "Write the file")

        out = _approve(db, task["id"])

        assert "blocked" in (out.get("result") or {}), out
        assert "plan" in str(out.get("result") or {}).lower()
        assert _status(db, task["id"]) == "planning"

    def test_neither_names_both(self, db: Path, tmp_path: Path):
        task = _a_task(db, tmp_path)

        said = str(_approve(db, task["id"]).get("result") or {}).lower()

        assert "plan" in said
        assert "checklist" in said


class TestApprovingWithThem:
    def test_a_plan_and_a_checklist_go_through(self, db: Path, tmp_path: Path):
        task = _a_task(db, tmp_path)
        _plan_for(tmp_path, task["id"])
        repo.tasks.add_checklist_item(db, int(task["id"]), "Write the file")

        _approve(db, task["id"])

        assert _status(db, task["id"]) == "approved"

    def test_a_task_with_no_project_uses_his_own_folder(self, db: Path, tmp_path: Path, monkeypatch):
        """A standalone errand goes through the same gate, so it has to be able to pass it."""
        monkeypatch.setattr(paths, "configured_root", lambda: tmp_path)
        task = repo.tasks.add_task(db, "A standalone errand", "normal", BRIEF, "planning")
        _plan_for(tmp_path, task["id"])
        repo.tasks.add_checklist_item(db, int(task["id"]), "Do it")

        _approve(db, task["id"])

        assert _status(db, task["id"]) == "approved"


class TestTheGateIsOnlyOnApproval:
    def test_starting_work_is_not_gated(self, db: Path, tmp_path: Path):
        """The gate is the approval, not every move afterwards. Re-checking it on `working` would
        make a task unstartable the moment somebody ticked the last checklist item off it."""
        task = _a_task(db, tmp_path)
        _plan_for(tmp_path, task["id"])
        repo.tasks.add_checklist_item(db, int(task["id"]), "Write the file")
        _approve(db, task["id"])

        run_tool("update_task", {"id": task["id"], "status": "working"}, db)

        assert _status(db, task["id"]) == "working"

    def test_dropping_it_is_never_gated(self, db: Path, tmp_path: Path):
        # Giving up on a task with no plan is exactly the case where insisting on one is absurd.
        task = _a_task(db, tmp_path)

        run_tool("update_task", {"id": task["id"], "status": "dropped"}, db)

        assert _status(db, task["id"]) == "dropped"


class TestTheGateCannotBeStepppedAround:
    """`add_task` reaches `approved` too, and the gate has to cover it or it is decorative.

    A brand-new task cannot be approved, and not merely because the check would fail: the plan is
    written to `.kith/work/task-<id>.md`, and the id does not exist until the row does. So there is
    no order of operations in which a task is born with an approved plan — the status is refused
    outright rather than gated, because the gate could never pass.
    """

    def test_a_task_cannot_be_created_already_approved(self, db: Path, tmp_path: Path):
        project = repo.projects.add_project(db, "App", "", str(tmp_path))
        out = run_tool(
            "add_task",
            {
                "goal": "Build the dashboard shell",
                "description": BRIEF,
                "project_id": int(project["id"]),
                "status": "approved",
            },
            db,
        )
        made = out.get("result") or {}
        # Filed, not refused — the work is still wanted. It just starts where everything starts.
        assert made.get("status") == "planning", out

    def test_and_says_why_rather_than_silently_moving_it(self, db: Path, tmp_path: Path):
        project = repo.projects.add_project(db, "App", "", str(tmp_path))
        out = run_tool(
            "add_task",
            {
                "goal": "Build the dashboard shell",
                "description": BRIEF,
                "project_id": int(project["id"]),
                "status": "approved",
            },
            db,
        )
        said = str(out.get("result") or {}).lower()
        assert "plan" in said
