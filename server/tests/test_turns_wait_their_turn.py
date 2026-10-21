"""Background turns take a slot, and wait for one when there is none.

Until 2026-10-21 a turn started the moment anything asked for one, on a thread of its own, with
nothing counting. The first real agent run started 24 agents three levels deep at once, and a
restart resumed every one of them in the same second. `kernel/turns` is the one place that decides
whether a turn Kith started himself — a reminder, a finished process, mail in the agent tree — runs
now or waits. Turns the person starts by typing never come through here and never wait.
"""

from __future__ import annotations

import contextvars
import threading

import pytest

from kith.kernel import session_context, turns


class _Runner:
    """Starts nothing real: records what it was asked to start, and says it started."""

    def __init__(self) -> None:
        self.started: list[turns.Request] = []
        self.answer: object = True

    def __call__(self, request: turns.Request):
        self.started.append(request)
        return self.answer


@pytest.fixture
def runner():
    run = _Runner()
    turns.install(run, limit=lambda: 2)
    return run


def _mail(conversation_id: str) -> turns.Request:
    return turns.Request(conversation_id, lane="agents", kind="mail")


def _wake(conversation_id: str, text: str = "a reminder") -> turns.Request:
    return turns.Request(conversation_id, lane="wake", kind="wake", text=text)


class TestASlot:
    def test_a_turn_starts_at_once_when_a_slot_is_free(self, runner):
        request = _mail("c1")
        assert turns.submit(request) == "started"
        assert runner.started == [request]
        assert turns.running() == 1

    def test_past_the_limit_turns_wait(self, runner):
        first, second, third = _mail("c1"), _mail("c2"), _mail("c3")
        assert [turns.submit(one) for one in (first, second, third)] == ["started", "started", "queued"]
        assert turns.queued() == ["c3"]
        assert turns.is_queued("c3")

        turns.ended(first)

        assert runner.started == [first, second, third]
        assert turns.queued() == []
        assert turns.running() == 2

    def test_the_limit_is_read_each_time(self, runner):
        limit = {"value": 1}
        turns.install(runner, limit=lambda: limit["value"])
        turns.submit(_mail("c1"))
        assert turns.submit(_mail("c2")) == "queued"

        limit["value"] = 2
        turns.submit(_mail("c3"))

        assert [one.conversation_id for one in runner.started] == ["c1", "c2"]

    def test_a_runner_that_starts_nothing_frees_the_slot(self, runner):
        runner.answer = None
        assert turns.submit(_mail("c1")) == "nothing"
        assert turns.running() == 0

    def test_a_runner_that_raises_frees_the_slot(self, runner):
        def explode(request):
            raise RuntimeError("no")

        turns.install(explode, limit=lambda: 1)
        assert turns.submit(_mail("c1")) == "nothing"
        assert turns.running() == 0


class TestTheQueue:
    def test_reminders_go_before_agents(self, runner):
        turns.install(runner, limit=lambda: 1)
        busy = _mail("busy")
        turns.submit(busy)
        turns.submit(_mail("agent"))
        turns.submit(_wake("chat"))

        turns.ended(busy)

        assert [one.conversation_id for one in runner.started] == ["busy", "chat"]
        assert turns.queued() == ["agent"]

    def test_one_queued_mail_turn_per_conversation(self, runner):
        """The turn takes every letter waiting when it starts, so a second request would start a
        turn with nothing to say."""
        turns.install(runner, limit=lambda: 0)
        assert turns.submit(_mail("c1")) == "queued"
        assert turns.submit(_mail("c1")) == "queued"
        assert turns.queued() == ["c1"]

    def test_wakes_are_not_merged(self, runner):
        """Two reminders are two things to say; the second becomes a steer once the first runs."""
        turns.install(runner, limit=lambda: 0)
        turns.submit(_wake("c1", "first"))
        turns.submit(_wake("c1", "second"))
        assert turns.queued() == ["c1", "c1"]

    def test_cancel_drops_what_is_queued_and_nothing_else(self, runner):
        turns.install(runner, limit=lambda: 1)
        running = _mail("c1")
        turns.submit(running)
        turns.submit(_mail("c2"))
        turns.submit(_wake("c2"))

        assert turns.cancel("c2") == 2
        assert turns.queued() == []
        assert turns.running() == 1

    def test_nothing_runs_until_a_runner_is_installed(self):
        request = _mail("c1")
        assert turns.submit(request) == "queued"

        run = _Runner()
        turns.install(run, limit=lambda: 2)

        assert run.started == [request]


class TestParking:
    """A turn waiting on someone else gives its slot back while it waits.

    Without this a full house of handlers each waiting on its own agents holds every slot, and the
    agents they are waiting for can never start: a deadlock that looks like a slow tree.
    """

    def test_a_parked_turn_gives_up_its_slot(self, runner):
        turns.install(runner, limit=lambda: 1)
        handler = _mail("handler")
        turns.submit(handler)
        child = _mail("child")
        assert turns.submit(child) == "queued"

        with session_context.working_in("handler"), turns.parked():
            assert runner.started == [handler, child]

    def test_coming_back_from_a_wait_does_not_wait(self, runner):
        """Back over the limit for a moment rather than blocked: re-taking a slot by waiting for one
        is the deadlock again, one step later."""
        turns.install(runner, limit=lambda: 1)
        handler = _mail("handler")
        turns.submit(handler)
        with session_context.working_in("handler"), turns.parked():
            turns.submit(_mail("child"))

        assert turns.running() == 2
        assert turns.submit(_mail("later")) == "queued"

    def test_parking_outside_a_background_turn_does_nothing(self, runner):
        turns.install(runner, limit=lambda: 1)
        turns.submit(_mail("busy"))
        with session_context.working_in("someone-typing"), turns.parked():
            assert turns.submit(_mail("other")) == "queued"


class TestContext:
    def test_a_turn_runs_in_its_own_context_not_the_dispatchers(self):
        """The thread that frees a slot is usually a finishing turn, pinned to its own worktree.
        The next turn must not start inside that."""
        marker: contextvars.ContextVar[str] = contextvars.ContextVar("marker", default="")
        seen: list[str] = []

        def run(request):
            seen.append(marker.get())
            return True

        turns.install(run, limit=lambda: 1)
        busy = _mail("busy")
        turns.submit(busy)
        turns.submit(_mail("next"))

        def finish_from_a_pinned_thread():
            marker.set("the finishing turn's worktree")
            turns.ended(busy)

        thread = threading.Thread(target=finish_from_a_pinned_thread)
        thread.start()
        thread.join()

        assert seen == ["", ""]
