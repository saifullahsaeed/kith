"""Approving a plan is the person's decision, and it has to be more than a sentence saying so.

`_verify_approvable` checked that the thing being approved was *complete* — a plan file and a
checklist — and nothing checked it was *theirs to approve*. Its own refusal text ends "They
approve it; you do not", which was prose in an error message with no code behind it.

Measured on task #104, 2026-08-13. One turn, seven calls: `add_task`, `write_file` for the plan,
four `add_checklist_item`, then `update_task(status='approved')`. Both existing conditions were
satisfied because he had just written both, so it passed, and he told his person "I created and
approved Task #104" about a plan they had not seen. Then he stopped and waited for them anyway —
the worst of both, their decision taken and the wait kept. Their next message was "go ahed
approved", approving something already marked approved.

`add_task` already refuses a task *born* approved, and the comment there worries about precisely
this: "left unhandled it was a way straight past `_verify_approvable`, which only guards
`update_task`". Filing and then approving is the same door with a different handle.

"Has a person seen this" cannot be read from inside a tool. "Has a turn passed" can, and it is
the honest proxy: the plan is handed over in chat, so the earliest anyone could answer is the
turn after the one that wrote it.
"""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo
from kith.kernel import session_context
from kith.tools import run_tool

#: Long enough to clear the "a task under a project needs a checkable finish line" guard.
BRIEF = (
    "Extend the connections UI and API so a plugin connection can move between firm and personal "
    "scope, and its non-secret values can be edited. Done when the scope transition tests pass."
)

STEPS = ("Trace the semantics", "Implement the API", "Add the card UI", "Test, build and push")


def _project(db: Path, directory: Path) -> int:
    return int(repo.projects.add_project(db, "Sadeef AI", "", str(directory))["id"])


def _write_plan(directory: Path, task_id: int) -> None:
    work = directory / ".kith" / "work"
    work.mkdir(parents=True, exist_ok=True)
    (work / f"task-{task_id}.md").write_text("# Plan\n\nTrace the scope semantics first.\n")


def _file_a_planned_task(db: Path, directory: Path) -> int:
    """Exactly what he did on #104: file it, write the plan, add every checklist item.

    Through `run_tool` rather than the repository, because the guard is about what *he* did and
    the repository is not a route he has. `repo.tasks.add_task` deliberately does not record
    anything in the turn's notes, which is why the older approval tests are untouched by this.
    """
    made = run_tool(
        "add_task",
        {"goal": "Manage connector scope", "description": BRIEF, "project_id": _project(db, directory)},
        db,
    )["result"]
    task_id = int(made["id"])
    _write_plan(directory, task_id)
    for step in STEPS:
        run_tool("add_checklist_item", {"id": task_id, "text": step}, db)
    return task_id


def _approve(db: Path, task_id: int) -> dict:
    return run_tool("update_task", {"id": task_id, "status": "approved"}, db)["result"]


def _status(db: Path, task_id: int) -> str:
    return str((repo.tasks.task_detail(db, task_id) or {}).get("status") or "")


class TestFilingAndApprovingInOneBreath:
    def test_he_cannot_approve_a_task_he_just_filed(self, db: Path, tmp_path: Path):
        with session_context.a_turn():
            task_id = _file_a_planned_task(db, tmp_path)
            out = _approve(db, task_id)

        assert out.get("blocked"), f"self-approval was allowed: {out}"
        assert "same turn" in out["blocked"]

    def test_the_task_is_left_where_it_was(self, db: Path, tmp_path: Path):
        """A refused transition must not half-apply."""
        with session_context.a_turn():
            task_id = _file_a_planned_task(db, tmp_path)
            _approve(db, task_id)
            assert _status(db, task_id) == "planning"

    def test_the_refusal_says_what_to_do_instead(self, db: Path, tmp_path: Path):
        """A refusal he cannot act on costs a round and leaves the person no wiser."""
        with session_context.a_turn():
            task_id = _file_a_planned_task(db, tmp_path)
            out = _approve(db, task_id)

        nxt = out.get("next", "")
        assert "planning" in nxt and "chat" in nxt, nxt
        assert "working" in nxt, "it must name the way out for work too small to need any of this"


class TestTheTurnAfterIsFine:
    def test_approval_works_once_a_turn_has_passed(self, db: Path, tmp_path: Path):
        """A pause for a person, not a ban. The next turn is that pause."""
        with session_context.a_turn():
            task_id = _file_a_planned_task(db, tmp_path)

        with session_context.a_turn():
            out = _approve(db, task_id)

        assert not out.get("blocked"), out
        assert _status(db, task_id) == "approved"

    def test_the_completeness_check_still_applies(self, db: Path, tmp_path: Path):
        """This guard is additional, not a replacement. No checklist is still no approval."""
        with session_context.a_turn():
            made = run_tool(
                "add_task",
                {"goal": "No checklist here", "description": BRIEF, "project_id": _project(db, tmp_path)},
                db,
            )["result"]
            _write_plan(tmp_path, int(made["id"]))

        with session_context.a_turn():
            out = _approve(db, int(made["id"]))

        assert out.get("blocked"), "the older completeness gate stopped working"
        assert "checklist" in out["blocked"]


class TestOutsideATurnItCannotFire:
    def test_a_script_or_a_test_is_not_blocked(self, db: Path, tmp_path: Path):
        """`turn_notes()` is a throwaway outside a turn, and that is the right answer: with no
        turn there is nobody who could have been asked, so there is nothing to be waiting for."""
        task_id = _file_a_planned_task(db, tmp_path)
        out = _approve(db, task_id)

        assert not out.get("blocked"), out
        assert _status(db, task_id) == "approved"
