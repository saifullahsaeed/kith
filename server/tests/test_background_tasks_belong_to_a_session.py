"""A background task belongs to the conversation that started it, and to no other.

The registry is one dict for the whole process, which was fine while nothing listed it: `check_process`
took a name, and a name you did not start was a name you did not know. Then two things made it
visible — the completion callback, which needs to know *which* chat to wake, and the panel's
Background section, which lists everything — and a global list is a leak: one conversation showing
another one's test run, in an app whose whole point is that two projects are two conversations.

Three separate isolations, and the third bites hardest:

* **listing** — the panel shows this conversation's tasks;
* **his own view** — `check_process` with no name lists his, not everybody's;
* **names** — `run-tests` is one global name, so a second conversation running its suite was told
  "already running a different test run" about a run belonging to a chat it cannot see.
"""

from __future__ import annotations

import time

import pytest

from kith.engine.run import processes as process_service
from kith.kernel import session_context


@pytest.fixture(autouse=True)
def a_clean_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(process_service, "_LOG_DIR", tmp_path / "processes", raising=False)
    process_service.processes._running.clear()
    yield
    for name in list(process_service.processes._running):
        try:
            process_service.processes.stop(name)
        except Exception:
            pass
    process_service.processes._running.clear()


def _start(conversation_id: str, name: str, command: str = "sleep 30") -> None:
    if conversation_id:
        with session_context.working_in(conversation_id):
            process_service.processes.start(command, name)
    else:
        process_service.processes.start(command, name)


class TestListingIsPerConversation:
    def test_it_shows_only_this_conversations_tasks(self):
        _start("c-1", "mine")
        _start("c-2", "theirs")

        listed = process_service.processes.check(conversation_id="c-1")

        assert [one["name"] for one in listed["running"]] == ["mine"]

    def test_the_other_conversation_sees_its_own(self):
        _start("c-1", "mine")
        _start("c-2", "theirs")

        listed = process_service.processes.check(conversation_id="c-2")

        assert [one["name"] for one in listed["running"]] == ["theirs"]

    def test_asking_for_everything_still_works(self):
        """The path with no session to narrow to — a script, a test, the desktop shell."""
        _start("c-1", "mine")
        _start("c-2", "theirs")

        listed = process_service.processes.check()

        assert {one["name"] for one in listed["running"]} == {"mine", "theirs"}

    def test_one_started_outside_any_conversation_is_nobodys(self):
        """Not another session's — but not this one's either, and a chat that did not start it
        cannot explain it or stop it sensibly."""
        _start("", "orphan")

        listed = process_service.processes.check(conversation_id="c-1")

        assert listed["running"] == []


class TestHisOwnViewIsNarrowedToo:
    def test_check_with_no_name_lists_only_this_conversations(self):
        """He should no more see another chat's running work than you should."""
        _start("c-1", "mine")
        _start("c-2", "theirs")

        with session_context.working_in("c-1"):
            listed = process_service.processes.check()

        assert [one["name"] for one in listed["running"]] == ["mine"]


class TestNamesDoNotCollideAcrossConversations:
    def test_two_conversations_may_each_have_a_run_tests(self):
        """One global `run-tests` meant the second chat to run its suite was refused, and pointed at
        `check_process('run-tests')` — a process belonging to a conversation it cannot see."""
        _start("c-1", "run-tests")
        _start("c-2", "run-tests")

        assert len(process_service.processes._running) == 2

    def test_each_conversation_reaches_its_own_by_that_name(self):
        _start("c-1", "run-tests")
        _start("c-2", "run-tests")

        with session_context.working_in("c-1"):
            mine = process_service.processes.check("run-tests")
        with session_context.working_in("c-2"):
            theirs = process_service.processes.check("run-tests")

        assert mine["name"] == "run-tests"
        assert theirs["name"] == "run-tests"

    def test_stopping_one_leaves_the_other_running(self):
        _start("c-1", "run-tests")
        _start("c-2", "run-tests")

        with session_context.working_in("c-1"):
            process_service.processes.stop("run-tests")

        time.sleep(0.1)
        with session_context.working_in("c-2"):
            still = process_service.processes.check("run-tests")
        assert still.get("alive") is True
