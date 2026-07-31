"""Stopping the step he is taking, without deciding whether he takes another.

"Stop" meant one thing and needed to mean two. It turned roaming off; a step already in
flight carried on regardless, for up to sixteen rounds, with the only other control on
screen greyed out while it ran. So watching him start down a wrong path meant watching him
finish it — the state where you most want a button is the state where there wasn't one.

The two are deliberately independent. Cancelling says "not this step"; stopping roaming says
"no more steps". Either without the other is a thing someone reasonably wants.
"""

from __future__ import annotations


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

    def test_stopping_roaming_does_not_kill_the_running_step(self):
        from kith.autonomy.runner import AutonomyRunner

        runner = AutonomyRunner()
        runner._running = True
        runner._tick_lock.acquire()
        try:
            runner.stop()
            assert runner._running is False
            # And the step in flight is untouched — "no more steps" is not "abandon this one",
            # which matters because a step that is nearly done should be allowed to land.
            assert not runner._cancel.is_set()
        finally:
            runner._tick_lock.release()

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
