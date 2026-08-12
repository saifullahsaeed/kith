"""The plan a task was approved on, on the task.

*"where the fuck is plan on task do you not attach plan on tasks"* — 2026-08-10 12:00, four
days after the planning gate shipped. A fair question with an embarrassing answer: the plan was
written to `.kith/work/task-<id>.md`, and read exactly once, when a status moved into
`planning` so the approval bar could carry it. `task_detail` returned the comment thread, the
checklist and the deliverables, so the drawer — the place you open to read a task — was the one
surface in the app where the plan did not appear.

It was also gated on `project_id`, which had stopped making sense: every task now goes through
the same `backlog → planning → planned` gate, including the standalone errand that used to skip
it, and a standalone task could not carry a plan at all.

The file on disk stays the single source of truth. It is read per request and never copied into
the database, so editing the file is still how you edit the plan — and a plan filed somewhere
else simply does not show, which is the same best-effort contract `_plan_doc` always had.
"""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo
from kith.infra.workspace import paths
from kith.tools import run_tool

PLAN = "# Plan\n\n1. Write the failing test\n2. Make it pass\n"


def _plan_in(directory: Path, task_id: int, body: str = PLAN) -> Path:
    work = directory / ".kith" / "work"
    work.mkdir(parents=True, exist_ok=True)
    doc = work / f"task-{task_id}.md"
    doc.write_text(body)
    return doc


class TestATaskInAProject:
    def test_the_plan_comes_back_with_the_task(self, db: Path, tmp_path: Path):
        project_dir = tmp_path / "a-project"
        project_dir.mkdir()
        project = repo.projects.add_project(db, "A project", "", str(project_dir))
        task = repo.tasks.add_task(db, "Do the thing", project_id=project["id"])
        _plan_in(project_dir, task["id"])

        detail = repo.tasks.task_detail(db, task["id"])

        assert detail["plan"] == PLAN

    def test_no_plan_filed_is_not_an_error(self, db: Path, tmp_path: Path):
        project_dir = tmp_path / "a-project"
        project_dir.mkdir()
        project = repo.projects.add_project(db, "A project", "", str(project_dir))
        task = repo.tasks.add_task(db, "Do the thing", project_id=project["id"])

        detail = repo.tasks.task_detail(db, task["id"])

        # Present and empty rather than missing, so the interface branches on content instead
        # of on whether the key exists.
        assert detail["plan"] == ""


class TestATaskWithNoProject:
    def test_it_can_carry_a_plan_too(self, db: Path, tmp_path: Path, monkeypatch):
        """The gate is uniform now, so this case cannot be the one that has no plan.

        Every task lands in `backlog` and nothing is workable until a plan for it has been
        approved — a standalone errand included. Requiring a project to *find* the plan made
        the one kind of task that never has a project the one kind that could not have one.
        """
        monkeypatch.setattr(paths, "configured_root", lambda: tmp_path)
        task = repo.tasks.add_task(db, "A standalone errand")
        _plan_in(tmp_path, task["id"])

        detail = repo.tasks.task_detail(db, task["id"])

        assert detail["plan"] == PLAN


class TestWhatHeSees:
    def test_view_task_carries_the_plan(self, db: Path, tmp_path: Path, monkeypatch):
        """His own surface, not only yours.

        He reaches the plan today by remembering the path and calling `read_file` — a round
        spent on something the task he just opened could have handed him.
        """
        monkeypatch.setattr(paths, "configured_root", lambda: tmp_path)
        task = repo.tasks.add_task(db, "A standalone errand")
        _plan_in(tmp_path, task["id"])

        out = run_tool("view_task", {"id": task["id"]}, db)

        assert out["result"]["plan"] == PLAN
