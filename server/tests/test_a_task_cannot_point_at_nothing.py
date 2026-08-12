"""A milestone id that is not a milestone, on the way in.

`set_task_milestone` was hardened against exactly this and says so in its own docstring: it used
to store whatever it was given and only look the milestone up to copy the project across, so a
stray value went straight to the column and the task pointed at a milestone that does not exist —
the roadmap could not gate it, the task page showed an empty milestone field, and nothing anywhere
said the link was broken.

`add_task` was left with the same hole, and it is the same three lines: look the milestone up, and
if it is there copy its project. If it is *not* there, fall through and store the id anyway.

Found on the real board, on screen. Tasks 91, 92 and 93 all carry `milestone_id = 30`, there is no
milestone 30, and their `project_id` is null — so the detail page renders the raw number 30 where a
milestone title belongs, and the status beside it says "Plan ready for your look".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith.infra.db import repositories as repo


class TestAddingOne:
    def test_a_milestone_that_does_not_exist_is_refused(self, db: Path):
        with pytest.raises(ValueError, match="milestone"):
            repo.tasks.add_task(db, "Do it", milestone_id=999)

    def test_nothing_is_written_when_it_refuses(self, db: Path):
        with pytest.raises(ValueError):
            repo.tasks.add_task(db, "Do it", milestone_id=999)
        # A task filed against nothing is worse than a task that failed to file: it sits on the
        # board looking placed, and the roadmap cannot gate it.
        assert repo.tasks.list_tasks(db) == []

    def test_a_real_milestone_still_carries_its_project_across(self, db: Path):
        project = repo.projects.add_project(db, "App", "")
        milestone = repo.projects.add_milestone(db, int(project["id"]), "Design")

        made = repo.tasks.add_task(db, "Do it", milestone_id=int(milestone["id"]))

        assert made["milestone_id"] == milestone["id"]
        assert made["project_id"] == project["id"]

    def test_no_milestone_at_all_is_still_fine(self, db: Path):
        """A one-off errand has no milestone and is never gated by a roadmap."""
        made = repo.tasks.add_task(db, "Buy milk")
        assert made["milestone_id"] is None

    def test_zero_means_no_milestone_rather_than_a_refusal(self, db: Path):
        # What an empty form field arrives as. `set_task_milestone` already treats it this way,
        # and the two must agree or the same value is valid on one path and fatal on the other.
        made = repo.tasks.add_task(db, "Buy milk", milestone_id=0)
        assert made["milestone_id"] in (None, 0)
