"""Closing a task he has not earned, now that `review` and `waiting` are gone.

`_verify_done` used to express both refusals as *status changes*: an unmet requirement moved the
task to `waiting`, and a clean pass with nobody watching moved it to `review`. Both then returned
`None`, which let the `done` through — the reclassification **was** the refusal.

Four statuses have no room for either column, and the reason is not brevity: a column waits to be
noticed, and four tasks once sat in "Waiting on you" that nobody knew were waiting. So the refusal
is now a refusal — the tool declines, the task stays where it is, and he is told to raise it with
his person in chat, where `ask` holds the turn until it is answered.

The property being protected is unchanged and is the whole point of the original: he wrote the
brief, chose the requirements, supplied the evidence and then graded it. On a real project that
passed a confident analysis document asserting the system used SQLite when it had already moved to
Postgres, every box ticked. Nothing here lets him mark his own homework; it only stops him filing
it in a tray nobody empties.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from kith.infra.db import repositories as repo
from kith.kernel import session_context
from kith.tools import run_tool

BRIEF = (
    "Done when the chooser lists every model from the catalog, remembers the one picked, "
    "and the frontend tests pass."
)


def _a_task(db: Path) -> dict:
    return repo.tasks.add_task(db, "Implement the frontend chooser", "high", BRIEF, "working")


def _close(db: Path, task_id: int, verification: list[dict] | None = None) -> dict:
    args: dict = {"id": task_id, "status": "done"}
    if verification is not None:
        args["verification"] = verification
    return run_tool("update_task", args, db)


def _status(db: Path, task_id: int) -> str:
    return str((repo.tasks.task_detail(db, task_id) or {}).get("status") or "")


class TestAnUnmetRequirement:
    def test_the_close_is_refused(self, db: Path):
        task = _a_task(db)
        out = _close(
            db,
            task["id"],
            [
                {"requirement": "lists every model", "met": True, "evidence": "catalog.py:40"},
                {"requirement": "frontend tests pass", "met": False, "evidence": "3 failing"},
            ],
        )
        # The designed refusal shape, not merely "something went wrong": before this, a
        # rewritten status happened to make the update invalid, so the call failed for an
        # incidental reason and the caller was told nothing it could act on.
        assert "blocked" in (out.get("result") or {}), out

    def test_the_task_is_not_marked_done(self, db: Path):
        task = _a_task(db)
        _close(
            db,
            task["id"],
            [{"requirement": "frontend tests pass", "met": False, "evidence": "3 failing"}],
        )
        assert _status(db, task["id"]) != "done"

    def test_it_stays_where_it_was_rather_than_moving_to_a_tray(self, db: Path):
        task = _a_task(db)
        _close(
            db,
            task["id"],
            [{"requirement": "frontend tests pass", "met": False, "evidence": "3 failing"}],
        )
        # `working`, not parked. There is no column that means "his person's turn" any more,
        # because the turn is taken by asking.
        assert _status(db, task["id"]) == "working"

    def test_it_tells_him_to_ask_rather_than_to_retry(self, db: Path):
        task = _a_task(db)
        out = _close(
            db,
            task["id"],
            [{"requirement": "frontend tests pass", "met": False, "evidence": "3 failing"}],
        )
        # Where to take it, not just that it was refused — the caller is a model that will
        # otherwise call the identical thing again. Asserted on "chat" rather than "ask",
        # because "task" contains "ask" and made this pass on any message at all.
        assert "chat" in str(out.get("result") or {}).lower(), out


class TestACleanPassWithNobodyWatching:
    def test_it_cannot_close_itself_unattended(self, db: Path):
        task = _a_task(db)
        met = [
            {"requirement": "lists every model", "met": True, "evidence": "catalog.py:40"},
            {"requirement": "frontend tests pass", "met": True, "evidence": "12 passed"},
        ]
        with session_context.nobody_watching():
            out = _close(db, task["id"], met)
        assert "blocked" in (out.get("result") or {}), out
        assert _status(db, task["id"]) != "done"

    def test_a_person_being_present_lets_it_close(self, db: Path):
        """In a conversation there is somebody to disagree, and the whole context to judge from.
        That is the difference the gate is drawn on — not the evidence, which he wrote either way."""
        task = _a_task(db)
        _close(
            db,
            task["id"],
            [
                {"requirement": "lists every model", "met": True, "evidence": "catalog.py:40"},
                {"requirement": "frontend tests pass", "met": True, "evidence": "12 passed"},
            ],
        )
        assert _status(db, task["id"]) == "done"


class TestTheirWordsComeBeforeTheClose:
    """His brief is his translation of what they asked. "Make sure that you do have option to
    configure everything … and seed some data to show me" became five tasks titled "… through the
    API", every one closed with every requirement met — and no screen could reach any of it. So a
    close that passes his own brief is shown their words once, and closes on the next call."""

    MET: ClassVar[list[dict]] = [
        {"requirement": "lists every model", "met": True, "evidence": "catalog.py:40"},
        {"requirement": "frontend tests pass", "met": True, "evidence": "12 passed"},
    ]

    def _filed_after(self, db: Path, said: str) -> tuple[str, dict]:
        from kith.services import conversations

        conversation = conversations.start(db, "testing")["id"]
        conversations.record(db, conversation, "user", said)
        conversations.record(
            db, conversation, "system", "Something you started in the background has finished."
        )
        return conversation, _a_task(db)

    def test_the_first_close_is_shown_what_they_said(self, db: Path):
        conversation, task = self._filed_after(db, "let me change the model from the settings screen")
        with session_context.working_in(conversation), session_context.a_turn():
            out = _close(db, task["id"], self.MET)
        result = out.get("result") or {}
        assert "blocked" in result, out
        assert result["what_they_said"] == "let me change the model from the settings screen"
        assert _status(db, task["id"]) != "done"

    def test_the_same_verification_then_closes_it(self, db: Path):
        """Once, not a loop: having been shown, he decides."""
        conversation, task = self._filed_after(db, "let me change the model from the settings screen")
        with session_context.working_in(conversation), session_context.a_turn():
            _close(db, task["id"], self.MET)
            _close(db, task["id"], self.MET)
        assert _status(db, task["id"]) == "done"

    def test_a_wake_note_is_not_taken_for_them(self, db: Path):
        """The last message before the task is the harness's; theirs is the one before it."""
        conversation, task = self._filed_after(db, "the real ask")
        with session_context.working_in(conversation), session_context.a_turn():
            out = _close(db, task["id"], self.MET)
        assert (out.get("result") or {}).get("what_they_said") == "the real ask"

    def test_a_task_filed_before_they_said_anything_here_just_closes(self, db: Path):
        from kith.services import conversations

        task = _a_task(db)
        conversation = conversations.start(db, "testing")["id"]
        conversations.record(db, conversation, "user", "something later and unrelated")
        with session_context.working_in(conversation), session_context.a_turn():
            _close(db, task["id"], self.MET)
        assert _status(db, task["id"]) == "done"


class TestTheRetiredWordsAreUnreachable:
    def test_a_task_cannot_be_moved_to_waiting(self, db: Path):
        task = _a_task(db)
        run_tool("update_task", {"id": task["id"], "status": "waiting"}, db)
        assert _status(db, task["id"]) == "working"

    def test_a_task_cannot_be_moved_to_review(self, db: Path):
        task = _a_task(db)
        run_tool("update_task", {"id": task["id"], "status": "review"}, db)
        assert _status(db, task["id"]) == "working"
