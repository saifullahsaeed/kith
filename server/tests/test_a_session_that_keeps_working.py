"""Work continues because a session is working, not because roaming is switched on.

Roaming was one switch over one board. On meant every open task anywhere was fair game; off
meant nothing happened at all. With two projects going there was no way to say "continue this
one" — and two at once is the point of sessions.

Underneath it sat a pile of machinery whose only job was guessing when he was allowed to act:
a roam interval, a 600-second idle backoff, a quiet period after you last spoke, a minimum
gap. Every one of them answered "when may he work without me", and a session answers that by
existing. What is left of the loop is: is something due, and is any session working.
"""

from __future__ import annotations

import sys

import pytest

from kith.infra.db import repositories as repo
from kith.services import conversations


def runner_on(db, monkeypatch):
    """A runner pointed at a temp database.

    `kith.autonomy.runner` resolves to the singleton instance rather than the module — the
    package __init__ re-exports it and shadows the submodule — so the module globals a method
    reads are only reachable through sys.modules.
    """
    module = sys.modules["kith.autonomy.runner"]
    monkeypatch.setattr(module, "AGENT_DB_PATH", db)
    return module.AutonomyRunner()


@pytest.fixture
def session_id(db):
    return conversations.start(db, "a piece of work")["id"]


class TestStartingAndStopping:
    def test_a_session_can_be_told_to_keep_working(self, db, session_id, monkeypatch):
        runner_on(db, monkeypatch).keep_working(session_id)
        assert repo.conversations.is_working(db, session_id)

    def test_and_told_to_stop(self, db, session_id, monkeypatch):
        runner = runner_on(db, monkeypatch)
        runner.keep_working(session_id)
        runner.rest(session_id)
        assert not repo.conversations.is_working(db, session_id)

    def test_two_sessions_work_independently(self, db, monkeypatch):
        one = conversations.start(db, "one")["id"]
        two = conversations.start(db, "two")["id"]
        runner = runner_on(db, monkeypatch)
        runner.keep_working(one)
        runner.keep_working(two)

        runner.rest(one)

        # The thing roaming could never express.
        assert not repo.conversations.is_working(db, one)
        assert repo.conversations.is_working(db, two)

    def test_stopping_nothing_in_particular_stops_everything(self, db, monkeypatch):
        ids = [conversations.start(db, f"s{n}")["id"] for n in range(3)]
        runner = runner_on(db, monkeypatch)
        for one in ids:
            runner.keep_working(one)

        runner.rest()

        assert not repo.conversations.working_sessions(db)


class TestWhatTheLoopAsks:
    def test_it_advances_when_a_session_is_working(self, db, session_id, monkeypatch):
        runner = runner_on(db, monkeypatch)
        assert runner._sessions_working() is False
        runner.keep_working(session_id)
        assert runner._sessions_working() is True

    def test_a_broken_database_does_not_stop_the_loop(self, db, monkeypatch):
        """The loop runs every second forever; it must not be able to throw.

        Reminders and standing jobs fire from the same thread, so an exception here would
        silently take those with it.
        """
        module = sys.modules["kith.autonomy.runner"]
        monkeypatch.setattr(module, "AGENT_DB_PATH", db / "nope" / "missing.db")
        assert module.AutonomyRunner()._sessions_working() is False


class TestWhatWentAway:
    def test_there_is_no_global_running_switch(self, db, monkeypatch):
        runner = runner_on(db, monkeypatch)
        # `running` said one thing about the whole machine. Work is per session now, so the
        # honest answer is a list of which ones.
        assert "running" not in runner.status()
        assert isinstance(runner.status()["working"], list)

    def test_the_guessing_knobs_are_gone(self):
        from kith.domain.tuning import TUNABLES

        keys = {knob.key for knob in TUNABLES}
        # Each of these existed to answer "when may he act without me". A session answers it.
        assert not keys & {"roam_interval", "idle_interval", "quiet_seconds"}

    def test_the_runner_no_longer_defers_to_you_typing(self, db, monkeypatch):
        # note_user_activity held ticks back for 25 seconds after you spoke, so he would not
        # interrupt. A session you started is not an interruption.
        assert not hasattr(runner_on(db, monkeypatch), "note_user_activity")


class TestItSurvivesARestart:
    def test_a_working_session_is_still_working_after_a_restart(self, db, session_id, monkeypatch):
        """`working` lives in the database, so closing the app does not silently abandon work.

        The alternative — clearing it on shutdown — means "keep working until I stop you"
        quietly becomes "keep working until something restarts", which is the kind of promise
        that is worse than not making it.
        """
        runner_on(db, monkeypatch).keep_working(session_id)
        fresh = runner_on(db, monkeypatch)  # as if the process had restarted
        assert fresh._sessions_working() is True


class TestItStopsWhenThereIsNothingLeft:
    def test_a_finished_session_stops_itself(self, db, session_id, monkeypatch):
        """Otherwise "keep going until I stop you" means "keep going forever".

        Measured before this existed: a task to write three haiku finished in a few steps and
        the loop then took thirty more, waking every second to rediscover an empty board. It
        cost almost nothing — an idle tick calls no model — but a session that never ends is
        a promise nobody would make on purpose, and it means the panel says he is working
        when he is not.
        """
        module = sys.modules["kith.autonomy.runner"]
        runner = runner_on(db, monkeypatch)
        runner.keep_working(session_id)
        assert repo.conversations.is_working(db, session_id)

        # An empty board: nothing pending, nothing due, no tasks, nothing to plan.
        monkeypatch.setattr(runner, "_new_pending", lambda: [])
        monkeypatch.setattr(runner, "_why_idle", lambda *_a: ("caught up", ""))
        monkeypatch.setattr(module.repo.tasks, "active_tasks", lambda _p: [])
        monkeypatch.setattr(module.repo.tasks, "tasks_awaiting_kith", lambda _p: [])
        monkeypatch.setattr(module.repo.reminders, "due_reminders", lambda _p, _n: [])
        monkeypatch.setattr(module.repo.schedules, "due_schedules", lambda _p, _n: [])
        monkeypatch.setattr(module.repo.projects, "milestones_needing_tasks", lambda _p: [])

        runner._tick()

        assert not repo.conversations.is_working(db, session_id)

    def test_it_does_not_stop_while_there_is_still_a_task(self, db, session_id, monkeypatch):
        module = sys.modules["kith.autonomy.runner"]
        runner = runner_on(db, monkeypatch)
        runner.keep_working(session_id)

        # A task exists, so the tick takes the work branch rather than the idle one. The
        # model call is not what is under test; that it does not clear `working` is.
        monkeypatch.setattr(
            module.repo.tasks, "active_tasks", lambda _p: [{"id": 1, "goal": "something", "status": "working"}]
        )
        monkeypatch.setattr(module.repo.tasks, "tasks_awaiting_kith", lambda _p: [])
        monkeypatch.setattr(module.repo.reminders, "due_reminders", lambda _p, _n: [])
        monkeypatch.setattr(module.repo.schedules, "due_schedules", lambda _p, _n: [])
        monkeypatch.setattr(runner, "_new_pending", lambda: [])
        monkeypatch.setattr(module, "stream_agent", lambda *a, **k: iter(()))

        runner._tick()

        assert repo.conversations.is_working(db, session_id)
