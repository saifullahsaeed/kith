"""A tick was the only judge of its own work, and there was no ceiling on how long it could try.

Two holes, one column closes both.

**Marking your own homework.** `_verify_done` makes him enumerate the brief and answer per item
with evidence before a task may close. It is genuinely good at catching work he *knows* is
incomplete, and structurally incapable of catching work he believes is complete and is not: he
wrote the brief, chose the requirements, supplied the evidence, and graded it. On a real project
that passed a finished, confident analysis document asserting the system used SQLite when it had
already moved to Postgres — every box ticked.

**No ceiling.** `focus_grind_limit` only fires when *nothing* moved, so a task that ticks one
checklist item every few ticks resets it forever. Measured on a real board: median 7 ticks a task,
but #9 took 20 and #15 took 18 — at roughly four minutes and a quarter-million prompt tokens each,
most of a working day on one item nobody had looked at.

`review` is where both land. No tick can pick it up (it is outside `TASK_ACTIVE`), so finished work
cannot be reworked for another twelve hours, and chat — which has the whole context, forty rounds
and a person in it — is shown the queue and asked to judge.
"""

from __future__ import annotations

from pathlib import Path

from kith.domain.enums import TASK_ACTIVE, TASK_STATUSES
from kith.infra.db import repositories as repo
from kith.services import memory_context, session_context
from kith.tools import run_tool

BRIEF = (
    "Done when the chooser lists every model from the catalog, remembers the one picked, "
    "and the frontend tests pass."
)


def _a_task(db: Path, status: str = "working") -> dict:
    return repo.tasks.add_task(
        db, "Implement the frontend chooser", "high", None, BRIEF, status, "kith", None, None
    )


def _claim_done(db: Path, task_id: int) -> dict:
    """Close a task the way he does: every requirement answered and met."""
    return run_tool(
        "update_task",
        {
            "id": task_id,
            "status": "done",
            "verification": [
                {"requirement": "lists every model", "met": True, "evidence": "catalog.py:40"},
                {"requirement": "frontend tests pass", "met": True, "evidence": "12 passed"},
            ],
        },
        db,
    )


class TestATickCannotCloseItsOwnWork:
    def test_an_unattended_pass_lands_in_review(self, db: Path):
        task = _a_task(db)
        with session_context.nobody_watching():
            _claim_done(db, task["id"])
        assert repo.tasks.task_detail(db, task["id"])["status"] == "review"

    def test_and_it_says_why_on_the_task(self, db: Path):
        """Otherwise it reads as a refusal, and he retries it."""
        task = _a_task(db)
        with session_context.nobody_watching():
            _claim_done(db, task["id"])
        notes = " ".join(c["body"] for c in repo.tasks.task_detail(db, task["id"])["comments"])
        assert "wrote the brief" in notes

    def test_in_a_conversation_done_still_means_done(self, db: Path):
        """There is a person present and the whole context to judge from. Routing chat through
        review too would just add a click to every close."""
        task = _a_task(db)
        _claim_done(db, task["id"])
        assert repo.tasks.task_detail(db, task["id"])["status"] == "done"

    def test_unattended_is_off_unless_the_tick_loop_says_otherwise(self):
        """The default has to be "someone is watching" — a script, a test or the API claiming to be
        a tick would silently stop closing tasks."""
        assert session_context.unattended() is False

    def test_an_unmet_requirement_still_goes_to_waiting_not_review(self, db: Path):
        """These are different outcomes and must not collapse: review is "I think this is done",
        waiting is "I cannot finish this". Only the second one needs them."""
        task = _a_task(db)
        with session_context.nobody_watching():
            run_tool(
                "update_task",
                {
                    "id": task["id"],
                    "status": "done",
                    "verification": [{"requirement": "lists every model", "met": False, "evidence": ""}],
                },
                db,
            )
        assert repo.tasks.task_detail(db, task["id"])["status"] == "waiting"


class TestReviewIsOutOfReachOfTicks:
    def test_it_is_a_real_status(self):
        assert "review" in TASK_STATUSES

    def test_a_tick_is_never_offered_it(self):
        assert "review" not in TASK_ACTIVE

    def test_active_tasks_leaves_it_out(self, db: Path):
        task = _a_task(db, status="review")
        assert task["id"] not in [t["id"] for t in repo.tasks.active_tasks(db)]

    def test_it_does_not_complete_its_milestone(self, db: Path):
        """Work waiting for a look is not finished work. A milestone rolling up on `review` would
        close a project on the strength of his own say-so — the exact thing this prevents."""
        project = repo.projects.add_project(db, "portal", "", "")
        milestone = repo.projects.add_milestone(db, project["id"], "the foundation", None)
        task = repo.tasks.add_task(
            db, "do the thing", "normal", None, BRIEF, "working", "kith", project["id"], milestone["id"]
        )
        repo.tasks.update_task(db, task["id"], status="review")
        assert repo.projects.get_project(db, project["id"])["status"] == "active"
        assert (
            next(m for m in repo.projects.list_milestones(db) if m["id"] == milestone["id"])["status"]
            == "todo"
        )


class TestChatIsShownTheQueue:
    def test_nothing_to_say_when_nothing_waits(self, db: Path):
        _a_task(db, status="working")
        assert memory_context.review_block(db) == ""

    def test_a_waiting_task_is_listed(self, db: Path):
        task = _a_task(db, status="review")
        block = memory_context.review_block(db)
        assert f"#{task['id']}" in block
        assert "Implement the frontend chooser" in block

    def test_it_says_how_to_close_or_return_it(self, db: Path):
        """A queue with no verb attached is a queue he reads and leaves alone."""
        _a_task(db, status="review")
        block = memory_context.review_block(db)
        assert "status='done'" in block
        assert "status='working'" in block

    def test_it_asks_for_a_spot_check_not_a_re_audit(self, db: Path):
        """Re-doing the work to check the work costs more attention than the delegation saved."""
        _a_task(db, status="review")
        assert "Do not re-do the work" in memory_context.review_block(db)

    def test_only_chat_is_given_it(self):
        """A tick seeing this would review its own work, which is the whole thing being fixed."""
        from kith.api.routes import chat

        source = chat.__loader__.get_source("kith.api.routes.chat")
        assert "memory_context.review_block(AGENT_DB_PATH)" in source
        runner = __import__("kith.autonomy.runner", fromlist=["x"])
        assert "review_block" not in runner.__loader__.get_source("kith.autonomy.runner")


class TestTheCeiling:
    def _ticks_on(self, db: Path, goal: str, count: int, mode: str = "start") -> None:
        for i in range(count):
            repo.messages.add_tick_log(
                db, f"2026-08-03T0{i % 10}:00:00", mode, f"working on: {goal}", [], 0, 0, 1.0, "ok"
            )

    def test_it_counts_only_ticks_that_worked_that_task(self, db: Path):
        self._ticks_on(db, "build the thing", 5)
        self._ticks_on(db, "something else", 9)
        assert repo.messages.times_worked(db, "build the thing") == 5

    def test_planning_and_replies_do_not_spend_the_budget(self, db: Path):
        """Only `start` mode is work. Charging a task for the tick that planned it would cut its
        allowance for reasons that have nothing to do with the task being hard."""
        self._ticks_on(db, "build the thing", 4, mode="start")
        self._ticks_on(db, "build the thing", 6, mode="reply")
        assert repo.messages.times_worked(db, "build the thing") == 4

    def test_a_task_nobody_has_worked_has_spent_nothing(self, db: Path):
        assert repo.messages.times_worked(db, "never touched") == 0

    def test_the_count_survives_a_restart(self, db: Path):
        """Read from the recorder, not an attribute — otherwise restarting the app is how you give
        a task another twelve hours."""
        self._ticks_on(db, "build the thing", 7)
        assert repo.messages.times_worked(db, "build the thing") == 7  # no in-memory state involved

    def test_past_the_cap_the_task_goes_to_review(self, db: Path, monkeypatch):
        import sys

        from kith.services import tuning

        runner_module = sys.modules["kith.autonomy.runner"]
        monkeypatch.setattr(runner_module, "AGENT_DB_PATH", db)
        tuning.apply({"task_tick_cap": 3})
        task = _a_task(db, status="working")
        self._ticks_on(db, task["goal"], 3)

        stopped = runner_module.AutonomyRunner()._over_the_task_cap(task["id"])
        assert stopped is True
        assert repo.tasks.task_detail(db, task["id"])["status"] == "review"

    def test_it_tells_them_and_keeps_the_work(self, db: Path, monkeypatch):
        import sys

        from kith.services import tuning

        runner_module = sys.modules["kith.autonomy.runner"]
        monkeypatch.setattr(runner_module, "AGENT_DB_PATH", db)
        tuning.apply({"task_tick_cap": 3})
        task = _a_task(db, status="working")
        self._ticks_on(db, task["goal"], 4)
        runner_module.AutonomyRunner()._over_the_task_cap(task["id"])

        stuck = [m for m in repo.messages.list_messages(db, 10) if m.get("kind") == "stuck"]
        assert len(stuck) == 1
        assert f"#{task['id']}" in stuck[0]["body"]
        notes = " ".join(c["body"] for c in repo.tasks.task_detail(db, task["id"])["comments"])
        assert "ticks on this" in notes
        assert "split it into something smaller" in notes

    def test_under_the_cap_nothing_happens(self, db: Path, monkeypatch):
        import sys

        from kith.services import tuning

        runner_module = sys.modules["kith.autonomy.runner"]
        monkeypatch.setattr(runner_module, "AGENT_DB_PATH", db)
        tuning.apply({"task_tick_cap": 12})
        task = _a_task(db, status="working")
        self._ticks_on(db, task["goal"], 5)
        assert runner_module.AutonomyRunner()._over_the_task_cap(task["id"]) is False
        assert repo.tasks.task_detail(db, task["id"])["status"] == "working"

    def test_zero_switches_it_off(self, db: Path, monkeypatch):
        import sys

        from kith.services import tuning

        runner_module = sys.modules["kith.autonomy.runner"]
        monkeypatch.setattr(runner_module, "AGENT_DB_PATH", db)
        tuning.apply({"task_tick_cap": 0})
        task = _a_task(db, status="working")
        self._ticks_on(db, task["goal"], 99)
        assert runner_module.AutonomyRunner()._over_the_task_cap(task["id"]) is False

    def test_the_default_leaves_the_median_task_room(self):
        """Median is 7 ticks; the runaways were 18 and 20. A cap that fired on typical work would
        turn every task into a review round-trip."""
        from kith.services import tuning

        assert 7 < tuning.value("task_tick_cap") < 18
