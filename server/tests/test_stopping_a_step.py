"""Stopping the step he is taking, without deciding whether he takes another.

"Stop" meant one thing and needed to mean two. It turned roaming off; a step already in
flight carried on regardless, for up to sixteen rounds, with the only other control on
screen greyed out while it ran. So watching him start down a wrong path meant watching him
finish it — the state where you most want a button is the state where there wasn't one.

The two are deliberately independent. Cancelling says "not this step"; stopping roaming says
"no more steps". Either without the other is a thing someone reasonably wants.
"""

from __future__ import annotations


def _runner_on(db, monkeypatch):
    """A runner pointed at a temp database.

    `kith.autonomy.runner` resolves to the singleton instance, not the module — the package
    __init__ re-exports it and shadows the submodule — so patching "kith.autonomy.runner.
    AGENT_DB_PATH" sets an attribute on the object and the method keeps reading the real one.
    The module itself is only reachable through sys.modules.
    """
    import sys

    module = sys.modules["kith.autonomy.runner"]
    monkeypatch.setattr(module, "AGENT_DB_PATH", db)
    return module.AutonomyRunner()


class TestTheTwoStopsAreDifferent:
    def test_cancelling_leaves_roaming_alone(self):
        from kith.autonomy.runner import AutonomyRunner

        runner = AutonomyRunner()
        runner._running = True
        runner._tick_lock.acquire()  # pretend a step is in flight
        try:
            runner.cancel_tick()
            # The whole point: you can abandon this step and still be roaming.
            assert runner._running is True
            assert runner._cancel.is_set()
        finally:
            runner._tick_lock.release()

    def test_resting_does_not_kill_the_running_step(self, db, monkeypatch):
        """Was "stopping roaming"; roaming is gone and this is now per session.

        The distinction it protects is unchanged: "no more steps" is not "abandon this one",
        and a step that is nearly done should be allowed to land.
        """
        from kith.infra.db import repositories as repo
        from kith.services import conversations

        opened = conversations.start(db, "a session")["id"]
        repo.conversations.set_working(db, opened, True)

        runner = _runner_on(db, monkeypatch)
        runner._tick_lock.acquire()
        try:
            runner.rest(opened)
            assert repo.conversations.is_working(db, opened) is False
            assert not runner._cancel.is_set()
        finally:
            runner._tick_lock.release()

    def test_one_session_stopping_leaves_the_others_working(self, db, monkeypatch):
        """The whole reason this is per session rather than one switch.

        Roaming could only ever be on for everything or off for everything, so with two
        projects going there was no way to say "stop this one".
        """
        from kith.infra.db import repositories as repo
        from kith.services import conversations

        one = conversations.start(db, "first")["id"]
        two = conversations.start(db, "second")["id"]
        repo.conversations.set_working(db, one, True)
        repo.conversations.set_working(db, two, True)

        _runner_on(db, monkeypatch).rest(one)

        assert repo.conversations.is_working(db, one) is False
        assert repo.conversations.is_working(db, two) is True

    def test_stopping_with_no_session_named_stops_all_of_them(self, db, monkeypatch):
        from kith.infra.db import repositories as repo
        from kith.services import conversations

        ids = [conversations.start(db, f"s{n}")["id"] for n in range(3)]
        for one in ids:
            repo.conversations.set_working(db, one, True)

        # What a person means by "stop" when they are not looking at a particular one.
        _runner_on(db, monkeypatch).rest()

        assert not repo.conversations.working_sessions(db)

    def test_cancelling_when_nothing_is_running_does_nothing(self):
        from kith.autonomy.runner import AutonomyRunner

        runner = AutonomyRunner()
        runner.cancel_tick()
        # Otherwise the flag sits armed and eats the next step, which would look like a
        # tick that silently refused to do anything.
        assert not runner._cancel.is_set()

    def test_a_new_step_clears_a_stale_cancel(self):
        from kith.autonomy.runner import AutonomyRunner

        runner = AutonomyRunner()
        runner._cancel.set()
        try:
            runner._safe_tick(forced=True)
        except Exception:
            pass
        assert not runner._cancel.is_set(), "a cancel leaked into the following step"


class TestTheStatusTheInterfaceReads:
    def test_stopping_is_reported(self):
        from kith.autonomy.runner import AutonomyRunner

        runner = AutonomyRunner()
        assert runner.status()["stopping"] is False
        runner._tick_lock.acquire()
        try:
            runner.cancel_tick()
            # The button reads this to say "Stopping…" rather than sitting there looking
            # like the click did nothing.
            assert runner.status()["stopping"] is True
        finally:
            runner._tick_lock.release()

    def test_every_status_field_survives_serialisation(self):
        """A field the schema does not declare is dropped from the response, silently.

        This is the third time. It cost `tokensUncached` and `lastTickUncached`, which
        shipped as zeros to an interface reading them correctly, and then it cost
        `stopping` — the cancel worked and the button could not tell. The comment in
        schemas.py warning about it was not enough, so here is a test instead.
        """
        from kith.autonomy.runner import AutonomyRunner
        from kith.schemas import AutonomyStatusSchema

        produced = set(AutonomyRunner().status())
        declared = set(AutonomyStatusSchema().fields)
        missing = sorted(produced - declared)
        assert not missing, (
            "these are in the status dict but not in AutonomyStatusSchema, so the API "
            f"drops them and the interface cannot see them: {missing}"
        )
