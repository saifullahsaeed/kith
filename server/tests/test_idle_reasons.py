"""Why he is idle, and when that is your problem.

"Nothing to do" has two very different causes and conflating them is how this arrangement
fails quietly: he may be finished, or he may be blocked on *you*. In the second case resting
silently is the worst thing he can do — you see "caught up", assume there is nothing to look
at, and the whole board sits waiting on an answer nobody knew was owed.
"""

from __future__ import annotations

import sys

import pytest

import kith.autonomy.runner  # noqa: F401 - registers the module in sys.modules
from kith.autonomy.runner import AutonomyRunner
from kith.infra.db import repositories as repo


@pytest.fixture
def runner(db, monkeypatch):
    """A runner pointed at the test database, without starting its thread.

    Reached through sys.modules on purpose. The module ends with `runner = AutonomyRunner()`,
    so the package attribute `kith.autonomy.runner` is that *instance* — both the dotted-string
    form and `from kith.autonomy import runner` hand you the object rather than the module, and
    the patch lands nowhere useful. sys.modules is the only view that is unambiguous.
    """
    module = sys.modules["kith.autonomy.runner"]
    monkeypatch.setattr(module, "AGENT_DB_PATH", db)
    return AutonomyRunner.__new__(AutonomyRunner)


class TestWhyIdle:
    def test_finished_reads_as_caught_up(self, runner, db):
        status, note = runner._why_idle()
        assert status == "caught up — resting"
        assert note == ""

    def test_a_question_he_asked_makes_you_the_reason(self, runner, db):
        task = repo.tasks.add_task(db, "check the numbers", "")
        repo.tasks.update_task(db, task["id"], status="waiting")
        status, note = runner._why_idle()
        assert "waiting on you" in status
        assert "check the numbers" in note

    def test_it_counts_them(self, runner, db):
        for goal in ("one", "two"):
            task = repo.tasks.add_task(db, goal, "")
            repo.tasks.update_task(db, task["id"], status="waiting")
        status, _ = runner._why_idle()
        assert "2 questions" in status

    def test_work_held_by_the_roadmap_is_reported_differently(self, runner, db):
        """Not the same thing as a question: nobody owes an answer, the order just is not
        finished. Saying "waiting on you" here would be blaming you for a plan."""
        project = repo.projects.add_project(db, "Ship it", "")["id"]
        first = repo.projects.add_milestone(db, project, "Design")["id"]
        second = repo.projects.add_milestone(db, project, "Build")["id"]
        repo.projects.add_dependency(db, second, first)
        repo.tasks.add_task(db, "build the thing", "", status="planned", project_id=project, milestone_id=second)
        status, note = runner._why_idle()
        assert "blocked" in status
        assert "milestones that aren't finished" in note

    def test_a_question_outranks_a_blocked_milestone(self, runner, db):
        """If he asked you something, that is the actionable one — say that."""
        project = repo.projects.add_project(db, "Ship it", "")["id"]
        first = repo.projects.add_milestone(db, project, "Design")["id"]
        second = repo.projects.add_milestone(db, project, "Build")["id"]
        repo.projects.add_dependency(db, second, first)
        repo.tasks.add_task(db, "build", "", status="planned", project_id=project, milestone_id=second)
        asked = repo.tasks.add_task(db, "answer me", "")
        repo.tasks.update_task(db, asked["id"], status="waiting")
        status, _ = runner._why_idle()
        assert "waiting on you" in status


class TestSayingItOnce:
    def test_it_records_a_message(self, runner, db):
        runner._say_youre_the_blocker("2 tasks are waiting on you")
        assert len(repo.messages.list_messages(db)) == 1

    def test_it_does_not_repeat_every_tick(self, runner, db):
        """A tick can fire every thirty seconds, and the same true sentence twenty times over
        is indistinguishable from a fault."""
        for _ in range(5):
            runner._say_youre_the_blocker("still waiting on you")
        assert len(repo.messages.list_messages(db)) == 1

    def test_it_is_the_kind_that_interrupts(self, runner, db):
        """Being the blocker is exactly what should reach past the default threshold."""
        runner._say_youre_the_blocker("waiting on you")
        assert repo.messages.list_messages(db)[0]["kind"] == "stuck"

    def test_a_failure_to_record_never_takes_down_the_tick(self, runner, monkeypatch):
        def explode(*args, **kwargs):
            raise RuntimeError("db gone")

        monkeypatch.setattr(sys.modules["kith.autonomy.runner"].repo.messages, "add_message", explode)
        runner._say_youre_the_blocker("something")  # must not raise
