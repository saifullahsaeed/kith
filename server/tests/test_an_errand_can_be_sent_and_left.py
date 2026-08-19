"""An errand you cannot afford to wait for, and the turn it comes back into.

Waiting is the default and is right for most of them: the answer is what the next tool call is
*for*, so handing back a receipt just means ending the turn to go and collect one. But a scout
reading forty pages of documentation should not hold a conversation, which is the same mistake
`run_tests` used to make by holding one on a half-hour test suite.

The tempting way to build this was the background-task path — `scheduler.wake_finished`. It is
wrong twice over and both are measurable. That timer runs every 30 seconds, so a twenty-second
errand would take up to fifty to be *noticed*, which is slower than simply waiting for it. And
it opens a new turn under `nobody_watching()`, so a finding asked for in a conversation you are
sitting in comes back to nobody, in a turn with a different permission gate, that has to work
out from the transcript what it was doing.

So it comes back into the turn that sent it, at a round boundary — the same place a steer lands,
and for the same reason: a round is the only moment the conversation is a list a provider will
accept.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from kith.config import default_config
from kith.kernel import session_context, stopping
from kith.services import agent_loop, errands
from kith.tools import delegation


@pytest.fixture(autouse=True)
def _a_clean_board():
    """No test inherits another's errands — the board is process-wide, like the meter."""
    errands.forget("conv-1")
    yield
    errands.forget("conv-1")


def _a_call(name: str, **arguments):
    return {"function": {"name": name, "arguments": json.dumps(arguments)}}


def _rounds(*script):
    calls: list[int] = []

    def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
        calls.append(1)
        text, tool_calls = script[min(len(calls) - 1, len(script) - 1)]
        if text:
            yield {"type": "delta", "role": "text", "text": text}
        yield {"type": "turn", "content": text, "tool_calls": list(tool_calls), "stats": {}}

    return fake


class TestSendingOneAndCarryingOn:
    def test_it_returns_a_receipt_rather_than_a_finding(self, db: Path, monkeypatch):
        monkeypatch.setattr(agent_loop, "_stream_once", _rounds(("Found it.", [])))

        with session_context.working_in("conv-1"):
            answer = delegation.delegate_subtask(db, {"objective": "read the docs", "wait": False})

        assert "findings" not in answer
        assert answer["sent"] == "read the docs"

    @pytest.mark.parametrize("said", [False, "false", "False", "no", "0"])
    def test_the_flag_is_read_the_way_models_actually_send_it(self, db: Path, monkeypatch, said):
        """A model sends a boolean as the string "false" often enough that reading it as truthy
        would silently wait for an errand the caller meant to send away — which looks exactly
        like the tool ignoring its own contract."""
        monkeypatch.setattr(agent_loop, "_stream_once", _rounds(("Found it.", [])))

        with session_context.working_in("conv-1"):
            answer = delegation.delegate_subtask(db, {"objective": "x", "wait": said})

        assert "sent" in answer

    @pytest.mark.parametrize("said", [None, True, "true", "yes"])
    def test_anything_short_of_a_clear_no_waits(self, db: Path, monkeypatch, said):
        monkeypatch.setattr(agent_loop, "_stream_once", _rounds(("Found it.", [])))
        args = {"objective": "x"} if said is None else {"objective": "x", "wait": said}

        with session_context.working_in("conv-1"):
            answer = delegation.delegate_subtask(db, args)

        assert answer["findings"] == "Found it."

    def test_a_conversation_cannot_send_out_more_than_it_can_hold(self, db: Path, monkeypatch):
        """Each errand is a live model stream. A turn that can open twenty of them can open
        twenty upstream connections on one thought."""
        monkeypatch.setattr(agent_loop, "_stream_once", _rounds(("Found it.", [])))

        for index in range(errands.MAX_OUT):
            errands.sent("conv-1", f"held-{index}", "x")

        with session_context.working_in("conv-1"):
            answer = delegation.delegate_subtask(db, {"objective": "one too many", "wait": False})

        assert "already have" in answer["error"]


class TestItComesBackIntoTheTurnThatSentIt:
    def test_a_finding_lands_at_the_next_round_boundary(self, db: Path, monkeypatch):
        """Not through the scheduler, and this is the assertion that says so: the finding is in
        the prompt of a *later round of the same turn*, not of a turn woken 30 seconds later."""
        seen: list[list[dict]] = []
        rounds = [("", [_a_call("web_search", query="a")]), ("done", [])]
        calls: list[int] = []

        def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
            seen.append([dict(m) for m in convo])
            calls.append(1)
            # The errand reports between round one and round two.
            if len(calls) == 1:
                errands.sent("conv-1", "e1", "where the ledger is")
                errands.deliver("conv-1", "e1", {"findings": "ledger.py:142", "looked_at": "grep"})
            text, tool_calls = rounds[min(len(calls) - 1, len(rounds) - 1)]
            yield {"type": "turn", "content": text, "tool_calls": list(tool_calls), "stats": {}}

        monkeypatch.setattr(agent_loop, "_stream_once", fake)
        from kith import tools

        host = tools.host(db, language_server=False, mcp=[])
        host = host.__class__(
            schemas=host.schemas, run=lambda *a, **k: {"ok": True, "result": "x"}, mcp_names=host.mcp_names
        )
        with session_context.working_in("conv-1"):
            events = list(
                agent_loop._run_turn(
                    [], default_config(), "host", db, host, max_rounds=4, conversation_id="conv-1"
                )
            )

        back = [e for e in events if e["type"] == "errand_back"]
        assert len(back) == 1
        assert "ledger.py:142" in back[0]["text"]
        # And it is really in what the model was sent, not merely announced to the interface.
        assert any("ledger.py:142" in str(m.get("content") or "") for m in seen[-1])

    def test_it_is_delivered_once(self, db: Path):
        errands.sent("conv-1", "e1", "x")
        errands.deliver("conv-1", "e1", {"findings": "the answer"})

        first = errands.collect("conv-1")
        second = errands.collect("conv-1")

        assert len(first) == 1
        assert second == []


class TestATurnDoesNotEndOnAnErrandStillOut:
    def test_it_waits_and_goes_round_again(self, db: Path, monkeypatch):
        """Ending here would throw away work the turn asked for — and throw it away invisibly,
        since a backgrounded errand's findings are not in the transcript."""
        calls: list[int] = []
        # Registered here rather than inside the round, so the reporter below cannot deliver
        # to an errand that does not exist yet — `deliver` drops what it does not recognise,
        # and a test that loses that race fails as "the loop did not wait".
        errands.sent("conv-1", "e1", "the long one")

        def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
            calls.append(1)
            if len(calls) == 2:
                # Round two only happens because the loop waited; prove it saw the finding.
                assert any("has come back" in str(m.get("content") or "") for m in convo)
            yield {"type": "turn", "content": "ok", "tool_calls": [], "stats": {}}

        def report_soon():
            time.sleep(0.05)
            errands.deliver("conv-1", "e1", {"findings": "it took a while"})

        from kith import tools

        monkeypatch.setattr(agent_loop, "_stream_once", fake)
        threading.Thread(target=report_soon, daemon=True).start()
        host = tools.host(db, language_server=False, mcp=[])
        with session_context.working_in("conv-1"):
            events = list(
                agent_loop._run_turn(
                    [], default_config(), "host", db, host, max_rounds=4, conversation_id="conv-1"
                )
            )

        assert len(calls) == 2, "the turn ended without waiting for the errand it sent"
        assert [e for e in events if e["type"] == "waiting_on_errands"]

    def test_one_that_reports_while_the_last_round_is_streaming_is_not_dropped(self, db: Path, monkeypatch):
        """The bug this shipped with, and the exact run that found it.

        Three errands sent with `wait=false`. The next round wrote "their results will arrive
        automatically as they finish" — and all three finished while it was writing that
        sentence. The drain that puts findings into the prompt runs at the *top* of a round, so
        it had already gone; and by the time the end-of-turn check ran they were back, which
        made them no longer *outstanding*. Zero out, so the turn ended, and `forget` dropped
        every one of them. Three sub-agents' worth of model calls, paid for, thrown away, with
        the reply promising the findings as the last thing the turn ever said.

        So "is anything still out" was the wrong question on its own. "Is anything back that
        nobody has read" is the other half, and it has to be asked first.
        """
        calls: list[int] = []
        errands.sent("conv-1", "e1", "the one that finishes fast")

        def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
            calls.append(1)
            if len(calls) == 1:
                # Reports *during* this round's own model call, after the drain has run.
                errands.deliver("conv-1", "e1", {"findings": "found while you were talking"})
            if len(calls) == 2:
                assert any("found while you were talking" in str(m.get("content") or "") for m in convo)
            yield {"type": "turn", "content": "ok", "tool_calls": [], "stats": {}}

        monkeypatch.setattr(agent_loop, "_stream_once", fake)
        from kith import tools

        with session_context.working_in("conv-1"):
            events = list(
                agent_loop._run_turn(
                    [],
                    default_config(),
                    "host",
                    db,
                    tools.host(db, language_server=False, mcp=[]),
                    max_rounds=4,
                    conversation_id="conv-1",
                )
            )

        assert len(calls) == 2, "the turn ended on a finding nobody had read"
        assert [e for e in events if e["type"] == "errand_back"]

    def test_a_wait_that_comes_back_with_nothing_ends_rather_than_spinning(self, db: Path, monkeypatch):
        """A hung errand delays a turn once. It does not get to burn the rest of the budget
        sending the same prompt again and again."""
        monkeypatch.setattr(errands, "WAIT_SECONDS", 0.1)
        calls: list[int] = []

        def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
            calls.append(1)
            if len(calls) == 1:
                errands.sent("conv-1", "e1", "never comes back")
            yield {"type": "turn", "content": "ok", "tool_calls": [], "stats": {}}

        monkeypatch.setattr(agent_loop, "_stream_once", fake)
        from kith import tools

        host = tools.host(db, language_server=False, mcp=[])
        with session_context.working_in("conv-1"):
            list(
                agent_loop._run_turn(
                    [], default_config(), "host", db, host, max_rounds=6, conversation_id="conv-1"
                )
            )

        assert len(calls) == 1

    def test_the_board_is_cleared_however_the_turn_ends(self, db: Path, monkeypatch):
        """An answer arriving after the turn that asked has no question in front of it."""
        monkeypatch.setattr(agent_loop, "_stream_once", _rounds(("done", [])))
        errands.sent("conv-1", "left-over", "x")
        errands.deliver("conv-1", "left-over", {"findings": "nobody collected this"})

        from kith import tools

        with session_context.working_in("conv-1"):
            list(
                agent_loop.stream_agent(
                    [],
                    default_config(),
                    "host",
                    db,
                    tools.host(db, language_server=False, mcp=[]),
                    max_rounds=2,
                    conversation_id="conv-1",
                )
            )

        assert errands.collect("conv-1") == []


class TestStopReachesInsideAnErrand:
    def test_a_stopped_errand_hands_back_what_it_had(self, db: Path, monkeypatch):
        """Before this, Stop did nothing for the length of an errand. The parent loop emits no
        events while a tool call runs, so its own between-events check could not fire, and a
        person clicking Stop watched four scouts carry on regardless."""
        rounds = [("looking", [_a_call("list_projects")]), ("looking", [_a_call("list_projects")])]
        calls: list[int] = []

        def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
            calls.append(1)
            text, tool_calls = rounds[min(len(calls) - 1, len(rounds) - 1)]
            yield {"type": "delta", "role": "text", "text": text}
            yield {"type": "turn", "content": text, "tool_calls": list(tool_calls), "stats": {}}

        monkeypatch.setattr(agent_loop, "_stream_once", fake)
        switch = stopping.arm("conv-1")
        switch.set()
        try:
            with session_context.working_in("conv-1"):
                answer = delegation.delegate_subtask(db, {"objective": "look at everything"})
        finally:
            stopping.disarm("conv-1", switch)

        assert "on request" in answer["note"]
        # Zero model calls, not one. The turn yields its context reading before it sends
        # anything, so the check fires on that — an errand sent into an already-stopped turn
        # costs nothing at all rather than one round.
        assert len(calls) == 0

    def test_an_errand_that_outlived_its_turn_stops_itself(self, db: Path, monkeypatch):
        """The leak this closes: `wait_for_all` gives up, the turn ends, and nothing else ever
        tells the errand — its stop switch is *disarmed* on the way out, so from inside, a
        finished turn and a healthy one look the same. It would run out its whole round budget,
        pay for every call, and hand its findings to a board that no longer exists. Money spent
        with nothing to show and no trace of the spending.
        """
        monkeypatch.setattr(errands, "WAIT_SECONDS", 0.1)
        rounds: list[int] = []
        started = threading.Event()
        may_finish = threading.Event()

        def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
            rounds.append(1)
            started.set()
            # Hold the first round open until the turn has given up and gone.
            may_finish.wait(5)
            yield {"type": "delta", "role": "text", "text": "looking"}
            yield {
                "type": "turn",
                "content": "looking",
                "tool_calls": [_a_call("list_projects")],
                "stats": {},
            }

        monkeypatch.setattr(agent_loop, "_stream_once", fake)
        with session_context.working_in("conv-1"):
            delegation.delegate_subtask(db, {"objective": "the long one", "wait": False})
        started.wait(5)
        # The turn ends here, taking the board with it.
        errands.forget("conv-1")
        may_finish.set()
        time.sleep(0.3)

        assert errands.abandoned("conv-1", "anything") is True
        assert len(rounds) == 1, "the errand kept working for a turn that had already gone"

    def test_nothing_is_stopping_when_no_turn_is_running(self):
        """The honest answer in a test or a script: nothing was asked to stop, because nothing
        was asked to start."""
        assert stopping.asked_to_stop("") is False
        assert stopping.asked_to_stop("no-such-conversation") is False
