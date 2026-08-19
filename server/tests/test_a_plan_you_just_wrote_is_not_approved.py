"""Approving is the one decision about a task that is his person's, and it had a second door.

`_verify_approvable` refused to approve a task **filed** in the same turn — the honest proxy for
"nobody has read this yet", since the plan is handed over in chat and the earliest anyone could
answer is the turn after. Right reasoning, wired to the wrong event.

Measured on tasks #110 and #111, 2026-08-19. Both rows already existed, so the gate never fired.
In one turn he added their checklists, wrote their plans, and moved each one
`planning -> approved -> working` in three consecutive rounds — then closed all three tasks with
deliverables titled "Engineered enterprise-grade Odoo plugin and connector runtime upgrades",
eleven checklist items ticked, and **not one file edited**. Eight minutes.

A task filed last week whose checklist was written ninety seconds ago is exactly as unseen as one
filed in this turn. So the marker moved from filing to planning.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith.infra import project_files
from kith.infra.db import repositories as repo
from kith.kernel import session_context
from kith.services import tasks as task_service
from kith.tools import tasks as task_tools


@pytest.fixture(autouse=True)
def workspace(tmp_path: Path, monkeypatch) -> Path:
    """A real folder, because a plan is a real file — `.kith/work/task-<id>.md` — and the gate
    reads it off disk rather than out of a column."""
    from kith.infra import workspace as sandbox

    monkeypatch.setattr(sandbox.paths, "configured_root", lambda: tmp_path)
    return tmp_path


def _a_task_from_before(db: Path) -> int:
    """A task that exists already, with a plan already written — the case the old gate could
    not see, because it only ever asked how old the *row* was."""
    made = repo.tasks.add_task(db, "Move Odoo behavior into the plugin runtime")
    task_id = int(made["id"])
    _write_plan(task_id)
    return task_id


def _write_plan(task_id: int) -> None:
    from kith.infra.workspace import paths

    where = project_files.plan_path(paths.root(), task_id)
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_text("# Plan\n\nA real plan, written down.")


class TestAPlanWrittenThisTurnCannotBeApprovedThisTurn:
    def test_the_door_that_got_used(self, db: Path):
        """The row is old. The checklist is ninety seconds old. Nobody has seen either."""
        task_id = _a_task_from_before(db)

        with session_context.a_turn():
            task_tools.add_checklist_item(db, {"id": task_id, "text": "Add dynamic field inspection"})
            refusal = task_service._verify_approvable(db, task_id)

        assert refusal is not None
        assert "in this same turn" in refusal["blocked"]
        assert "planning" in refusal["next"]

    def test_the_refusal_says_why_being_old_does_not_help(self, db: Path):
        task_id = _a_task_from_before(db)

        with session_context.a_turn():
            task_tools.add_checklist_item(db, {"id": task_id, "text": "A step"})
            refusal = task_service._verify_approvable(db, task_id)

        assert "older than the plan" in refusal["blocked"]

    def test_next_turn_it_can_be_approved(self, db: Path):
        """The gate is about a turn passing, not about freezing the task. Once his person has had
        a turn in which to answer, approving is allowed again."""
        task_id = _a_task_from_before(db)

        with session_context.a_turn():
            task_tools.add_checklist_item(db, {"id": task_id, "text": "A step"})

        with session_context.a_turn():  # the next turn — they have had a chance to read it
            assert task_service._verify_approvable(db, task_id) is None


class TestWhatItMustNotBlock:
    def test_a_task_planned_earlier_is_untouched(self, db: Path):
        task_id = _a_task_from_before(db)
        repo.tasks.add_checklist_item(db, task_id, "Written in some earlier turn")

        with session_context.a_turn():
            assert task_service._verify_approvable(db, task_id) is None

    def test_outside_a_turn_nothing_fires(self, db: Path):
        """A test or a script has nobody to have asked, so there is nothing to wait for —
        `turn_notes()` is a throwaway dict there and this cannot trigger."""
        task_id = _a_task_from_before(db)
        repo.tasks.add_checklist_item(db, task_id, "A step")

        assert task_service._verify_approvable(db, task_id) is None

    def test_a_task_with_no_checklist_is_refused_for_the_original_reason(self, db: Path):
        """The completeness half of the gate is unchanged: a plan with no checklist approves
        prose with no way to see progress afterwards."""
        made = repo.tasks.add_task(db, "Something")
        _write_plan(int(made["id"]))

        refusal = task_service._verify_approvable(db, int(made["id"]))

        assert refusal is not None
        assert "checklist" in refusal["blocked"]
