"""The task page dates the plan by the file, not by the task.

A plan is written after a task is filed, sometimes days after, and revised after that. The page's
activity put "filed with its plan" at the task's creation time because nothing said when the plan
itself was written.
"""

from __future__ import annotations

from kith.infra import project_files
from kith.infra.db import repositories as repo


def test_a_plan_carries_its_files_time(db, tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    project = repo.projects.add_project(db, "P", "", directory=str(folder))
    task = repo.tasks.add_task(db, "do it", "normal", "", "planning", "user", project["id"])

    assert repo.tasks.task_detail(db, task["id"])["plan_modified"] == ""

    file = project_files.plan_path(folder, task["id"])
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("# Plan\n")

    detail = repo.tasks.task_detail(db, task["id"])
    assert detail["plan"].startswith("# Plan")
    assert detail["plan_modified"].startswith("20")
