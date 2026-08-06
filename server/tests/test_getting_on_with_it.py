"""Filing work makes it happen, and changing it mid-flight is noticed.

Three complaints, one shape: things you did in the interface reached the database and
stopped there. The loop only wakes for a session that is *working*, and that was settable
only by a button — so filing a task, promoting one out of the backlog, and writing a comment
all produced a row and no action. You asked for a thing, he wrote it down, and you both
waited.

The fourth is the mirror of it: a tick reads the board once and then runs for a couple of
minutes, so blocking a task while he was on it changed nothing until he had finished doing
the thing you blocked.
"""

from __future__ import annotations

import sys

import pytest

from kith.infra.db import repositories as repo
from kith.services import session_context


def runner_module():
    """The module, not the singleton that shadows it. See conftest."""
    return sys.modules["kith.autonomy.runner"]


@pytest.fixture
def woken(monkeypatch):
    """Record who gets woken, without starting anything."""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        runner_module().AutonomyRunner,
        "nudge",
        lambda self, conversation_id="", why="": calls.append((conversation_id, why)),
    )
    return calls


@pytest.fixture
def conversation(db):
    row = repo.conversations.create(db, "c-1", "A chat", "kith-abc")
    return row["id"] if isinstance(row, dict) else "c-1"


class TestFilingATask:
    def test_a_standalone_task_lands_in_the_backlog_and_does_not_wake_him(
        self, db, conversation, woken, monkeypatch
    ):
        """Filing an errand used to be enough to ask for it to happen. Now nothing is
        pickable until it has been through the planning-a-task skill and approved —
        a standalone errand is no longer a special case that skips that gate."""
        from kith.tools import registry

        monkeypatch.setattr("kith.tools.tasks.repo", repo)
        with session_context.working_in(conversation):
            made = registry.get("add_task").run(db, {"goal": "Fix the login bug"})

        assert made["status"] == "backlog"
        assert not woken, "filing a task should not skip the planning gate"

    def test_a_task_under_a_milestone_lands_in_the_backlog(self, db, conversation, woken):
        """A roadmap is not started halfway through writing it — he used to pick up task one
        while the third was still being typed."""
        from kith.tools import registry

        project = repo.projects.add_project(db, "App", "an app")
        milestone = repo.projects.add_milestone(db, int(project["id"]), "Foundations")

        with session_context.working_in(conversation):
            made = registry.get("add_task").run(
                db,
                {
                    "goal": "The seed script loads fixtures",
                    "description": "running `python seed.py` exits 0 and the table has rows",
                    "milestone_id": int(milestone["id"]),
                },
            )

        assert made["status"] == "backlog"
        assert not woken, "scaffolding a roadmap should not start him working"

    def test_an_explicit_status_still_wins(self, db, conversation, woken):
        from kith.tools import registry

        project = repo.projects.add_project(db, "App", "an app")
        milestone = repo.projects.add_milestone(db, int(project["id"]), "Foundations")

        with session_context.working_in(conversation):
            made = registry.get("add_task").run(
                db,
                {
                    "goal": "Do the urgent thing now",
                    "description": "the command `make urgent` exits 0 when this is done",
                    "milestone_id": int(milestone["id"]),
                    "status": "planned",
                },
            )

        assert made["status"] == "planned"
        assert woken

    def test_a_backlog_task_is_invisible_to_the_picker(self, db):
        """The status has always existed and always been excluded. What was missing was
        anything defaulting to it."""
        repo.tasks.add_task(db, "Later", "normal", None, "", "backlog", "kith")
        repo.tasks.add_task(db, "Now", "normal", None, "", "planned", "kith")

        goals = {task["goal"] for task in repo.tasks.active_tasks(db)}

        assert goals == {"Now"}


class TestPromotingOutOfTheBacklog:
    def test_approving_a_plan_wakes_him(self, db, conversation, woken):
        """The other half of scaffolding into backlog: approving a plan into `planned`
        is what says go."""
        from kith.tools import registry

        made = repo.tasks.add_task(db, "Later", "normal", None, "", "backlog", "kith")

        with session_context.working_in(conversation):
            registry.get("update_task").run(db, {"id": int(made["id"]), "status": "planned"})

        assert woken

    def test_moving_it_back_to_backlog_does_not(self, db, conversation, woken):
        from kith.tools import registry

        made = repo.tasks.add_task(db, "Now", "normal", None, "", "planned", "kith")

        with session_context.working_in(conversation):
            registry.get("update_task").run(db, {"id": int(made["id"]), "status": "backlog"})

        assert not woken


class TestWritingToHimOnATask:
    def test_a_comment_wakes_the_session_on_that_project(self, db, woken):
        """A comment on a task that was not `waiting` produced no reply, no acknowledgement,
        and no sign it had been read — which reads as not being listened to."""
        from kith.services import brain

        project = repo.projects.add_project(db, "App", "an app")
        repo.conversations.create(db, "c-app", "Working on App", "kith-1")
        repo.conversations.set_project(db, "c-app", int(project["id"]))
        task = repo.tasks.add_task(db, "Ship it", "normal", None, "", "planned", "kith", int(project["id"]))

        brain.create(db, "task_comment", {"task_id": int(task["id"]), "body": "any progress?"})

        assert woken, "writing to him on a task did nothing"
        assert woken[0][0] == "c-app", f"it woke the wrong session: {woken}"

    def test_the_comment_is_still_saved_when_waking_fails(self, db, monkeypatch):
        """Bookkeeping must never turn writing a comment into an error."""
        from kith.services import brain

        def explode(*_a, **_k):
            raise RuntimeError("no")

        monkeypatch.setattr(runner_module().AutonomyRunner, "nudge", explode)
        task = repo.tasks.add_task(db, "Ship it", "normal", None, "", "planned", "kith")

        brain.create(db, "task_comment", {"task_id": int(task["id"]), "body": "hello"})

        comments = repo.tasks.list_task_comments(db, int(task["id"]))
        assert [one["body"] for one in comments] == ["hello"]

    def test_it_is_found_as_something_he_owes_an_answer_to(self, db):
        task = repo.tasks.add_task(db, "Ship it", "normal", None, "", "planned", "kith")
        repo.tasks.add_task_comment(db, int(task["id"]), "user", "any progress?")

        owed = {one["id"] for one in repo.tasks.tasks_awaiting_kith(db)}

        assert int(task["id"]) in owed


class TestWakingIsNotSpending:
    def test_a_nudge_does_not_reset_the_token_budget(self):
        """`keep_working` zeroes the meter so a resumed session is not instantly capped.
        Doing that on every task filed would mean the cap could never be reached."""
        runner = runner_module().AutonomyRunner()
        runner._session_tokens["c-1"] = 50_000

        runner.nudge("c-1", "something happened")

        assert runner._session_tokens["c-1"] == 50_000

    def test_a_session_stopped_for_budget_stays_stopped(self):
        """The cap exists to be hit, and an event arriving afterwards is not a reason to
        spend past it."""
        runner = runner_module().AutonomyRunner()
        runner._session_capped.add("c-1")
        started: list[str] = []
        runner.ensure_loop = lambda: started.append("loop")  # type: ignore[method-assign]

        runner.nudge("c-1", "something happened")

        assert not started

    def test_nudging_nobody_is_harmless(self):
        runner_module().AutonomyRunner().nudge("", "no session here")


class TestNudgingNoLongerStartsUnattendedWork:
    """Ticks are isolated from chat, and this is the half that isn't a button: filing a task
    or writing a comment still decides something is worth getting on with (the `woken`
    fixture above proves that wiring is intact), but `nudge` itself no longer acts on it."""

    def test_it_does_not_flip_the_conversation_to_working(self, db):
        repo.conversations.create(db, "c-1", "A chat", "kith-1")

        runner_module().AutonomyRunner().nudge("c-1", "a task became actionable")

        # The raw repository row stores it as SQLite's 0/1, not a Python bool.
        assert not repo.conversations.get(db, "c-1")["working"]

    def test_it_does_not_start_the_loop(self):
        runner = runner_module().AutonomyRunner()
        started: list[str] = []
        runner.ensure_loop = lambda: started.append("loop")  # type: ignore[method-assign]

        runner.nudge("c-1", "a task became actionable")

        assert not started


class TestATaskThatChangesUnderHim:
    def test_a_blocked_task_stops_the_step(self, db, monkeypatch):
        """Pulling a task out from under him is a clear instruction to stop touching it, and
        it should not have to wait for him to finish the thing you blocked."""
        runner = runner_module().AutonomyRunner()
        monkeypatch.setattr(runner_module(), "AGENT_DB_PATH", db)
        task = repo.tasks.add_task(db, "In flight", "normal", None, "", "working", "kith")

        assert runner._task_moved_on(int(task["id"])) is False

        repo.tasks.update_task(db, int(task["id"]), "waiting")
        runner._last_freshness_check = 0.0
        assert runner._task_moved_on(int(task["id"])) is True

    def test_a_deleted_task_stops_the_step(self, db, monkeypatch):
        runner = runner_module().AutonomyRunner()
        monkeypatch.setattr(runner_module(), "AGENT_DB_PATH", db)
        assert runner._task_moved_on(999_999) is True

    def test_it_is_not_asked_twice_in_the_same_second(self, db, monkeypatch):
        """One indexed read, but a tick fires events continuously."""
        runner = runner_module().AutonomyRunner()
        monkeypatch.setattr(runner_module(), "AGENT_DB_PATH", db)
        reads = []
        real = repo.tasks.task_detail
        monkeypatch.setattr(repo.tasks, "task_detail", lambda *a, **k: (reads.append(1), real(*a, **k))[1])
        task = repo.tasks.add_task(db, "In flight", "normal", None, "", "working", "kith")

        for _ in range(20):
            runner._task_moved_on(int(task["id"]))

        assert len(reads) == 1, f"it read the database {len(reads)} times in a tight loop"

    def test_a_lookup_that_fails_does_not_interrupt_the_work(self, db, monkeypatch):
        """Interrupting real work because a status read hiccupped is worse than finishing a
        step that should have stopped."""
        runner = runner_module().AutonomyRunner()
        monkeypatch.setattr(runner_module(), "AGENT_DB_PATH", db)
        monkeypatch.setattr(
            repo.tasks, "task_detail", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("db gone"))
        )

        assert runner._task_moved_on(1) is False
