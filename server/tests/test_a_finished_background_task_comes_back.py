"""A background command that finishes says so, in the chat that started it.

Running something long already worked — `start_process` returns straight away and `check_process`
reads what is new. What was missing was the other end: nothing noticed a process *finishing*, so the
only way to learn was to ask, and asking is a full round at whatever the prompt costs. Over two days
that was 371 `run_tests`/`check_process` calls, 24 of them byte-identical, 16 checks on one process,
and 12 reminder-driven turns at $2.19 — all of it spent on "done yet".

Holding the turn open instead was the wrong answer and was rejected: a half-hour suite would block
the conversation for half an hour, and the point of backgrounding is to go and do something else.

So finishing wakes the conversation, through the same `_continue` a reminder uses — a real turn, in
the transcript, rather than a line in a live feed that is gone when nobody is looking. He reads the
exit code and the tail, and carries on from there.
"""

from __future__ import annotations

import pytest

from kith.kernel import session_context
from kith.services import scheduler
from kith.services.code import processes as process_service


def _resume(conversation_id: str, trigger: str) -> None:
    """How a finished task wakes its conversation. Handed in rather than imported — running a
    turn is the chat route's job, and this is a service."""


@pytest.fixture(autouse=True)
def a_clean_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(process_service, "_LOG_DIR", tmp_path / "processes", raising=False)
    process_service.processes._running.clear()
    yield
    process_service.processes._running.clear()


@pytest.fixture
def woken(monkeypatch):
    """Every `(conversation_id, notes)` a completion would have woken, instead of a real turn."""
    calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(scheduler, "_continue", lambda cid, notes, resume: calls.append((cid, list(notes))))
    return calls


def _finished(name: str, command: str = "true") -> None:
    """Start something that exits immediately, and wait for it to actually be gone."""
    import time

    with session_context.working_in("c-1"):
        process_service.processes.start(command, name)
    for _ in range(200):
        if not process_service.processes._find(name, "c-1").running:
            return
        time.sleep(0.01)
    raise AssertionError(f"{name} never exited")


class TestFinishingWakesTheChat:
    def test_a_finished_process_wakes_the_conversation_that_started_it(self, woken):
        _finished("quick")
        process_service.finished_since_last_look(_resume)
        assert [cid for cid, _ in woken] == ["c-1"]

    def test_the_note_says_what_finished_and_how(self, woken):
        _finished("quick")
        process_service.finished_since_last_look(_resume)
        note = woken[0][1][0]
        assert "quick" in note, "which task"
        assert "finished" in note, "and how it ended — a clean exit needs no number"

    def test_a_failure_says_so(self, woken):
        _finished("bad", command="exit 3")
        process_service.finished_since_last_look(_resume)
        assert "3" in woken[0][1][0]

    def test_it_only_wakes_once(self, woken):
        """The watcher runs every thirty seconds. A process that finished stays finished, and
        reporting it on every pass would wake the conversation for ever."""
        _finished("quick")
        process_service.finished_since_last_look(_resume)
        process_service.finished_since_last_look(_resume)
        assert len(woken) == 1

    def test_two_finishing_together_are_one_turn(self, woken):
        """Grouped the way reminders are: two completions in one chat should be one continuation
        with both notes, not two replies talking past each other."""
        _finished("one")
        _finished("two")
        process_service.finished_since_last_look(_resume)
        assert len(woken) == 1
        assert len(woken[0][1]) == 2


class TestWhatDoesNotWakeAnything:
    def test_something_still_running_does_not(self, woken):
        with session_context.working_in("c-1"):
            process_service.processes.start("sleep 30", "slow")
        process_service.finished_since_last_look(_resume)
        assert woken == []
        process_service.processes.stop("slow")

    def test_one_started_outside_a_conversation_does_not(self, woken):
        """A script, a test, the setup for something else — there is no chat to wake."""
        process_service.processes.start("true", "orphan")
        import time

        for _ in range(200):
            if not process_service.processes._find("orphan", "").running:
                break
            time.sleep(0.01)
        process_service.finished_since_last_look(_resume)
        assert woken == []

    def test_one_you_stopped_yourself_does_not(self, woken):
        """You already know: you are the one who stopped it."""
        with session_context.working_in("c-1"):
            process_service.processes.start("sleep 30", "cancelled")
        process_service.processes.stop("cancelled")
        process_service.finished_since_last_look(_resume)
        assert woken == []


class TestTheProcessRemembersItsChat:
    def test_start_stamps_the_conversation(self):
        with session_context.working_in("c-9"):
            process_service.processes.start("sleep 30", "mine")
        assert process_service.processes._find("mine", "c-9").conversation_id == "c-9"
        process_service.processes.stop("mine")

    def test_it_is_reported_so_the_panel_can_show_it(self):
        with session_context.working_in("c-9"):
            out = process_service.processes.start("sleep 30", "mine")
        assert out.get("name") == "mine"
        listed = process_service.processes.check()
        assert "mine" in str(listed)
        process_service.processes.stop("mine")
