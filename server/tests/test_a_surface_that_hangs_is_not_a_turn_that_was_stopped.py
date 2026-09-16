"""Three ways a call to a surface can end, and the two that used to be indistinguishable.

`calls.ask` parks a turn on a live frame. It can end four ways — an answer, a frame that did not
answer, a *window* that is not there any more, and a person pressing stop — and the sentence he
gets has to be the right one, because he acts on it.

Two of them collapsed into one. `reply(ok=False)` — which is what the renderer sends when its own
clock fires on a hung frame — stored `reply = None`, and `None` is also what a stopped turn
leaves behind. So the commonest surface failure there is reached the model as *"The turn was
stopped before that was answered"*: a sentence about something the person did, for something the
person had not done.

The second half is worse and is the same bug. That path returned before touching `_MISSES`, so
the counter behind `UNRESPONSIVE_AFTER` — the whole point of which is to stop every later call
waiting out a full deadline against a frame that has given up — could only ever be incremented
by the *server's* clock, which only runs when there is no renderer. The circuit breaker written
for a hung frame in an open window was unreachable in exactly that case.
"""

from __future__ import annotations

import threading

import pytest

from kith.kernel import live_turns
from kith.services.plugins import calls


@pytest.fixture(autouse=True)
def somebody_is_here(monkeypatch):
    """A person and a live turn, which `ask` checks before it parks anything."""
    monkeypatch.setattr(live_turns, "current", lambda _c: object())
    monkeypatch.setattr("kith.kernel.session_context.unattended", lambda: False)
    calls.forget_misses()
    yield
    calls.forget_misses()


def a_call(**over) -> calls.Call:
    return calls.Call(
        id=over.pop("id", "call-1"),
        conversation_id="c-1",
        plugin="notes",
        command="ask_it",
        view="board",
        instance="",
        args={},
        timeout_ms=over.pop("timeout_ms", 250),
        **over,
    )


def answered_with(call: calls.Call, *, ok: bool, value: dict | None = None) -> dict:
    """Put the call, and have something answer it from another thread the way a renderer does."""

    def responder():
        for _ in range(500):
            if calls.held(call.id) is not None:
                calls.reply(call.id, value, ok=ok)
                return
            threading.Event().wait(0.002)

    hand = threading.Thread(target=responder, daemon=True)
    hand.start()
    answer = calls.ask(call)
    hand.join(timeout=2)
    return answer


def test_a_surface_that_answers_is_an_answer(monkeypatch):
    answer = answered_with(a_call(), ok=True, value={"said": "yes"})

    assert answer["ok"] is True
    assert answer["result"]["value"] == {"said": "yes"}


def test_a_frame_that_did_not_answer_says_so_in_its_own_words():
    """**The bug.** The renderer answered for a hung frame, and he was told the turn was stopped."""
    answer = answered_with(a_call(), ok=False)

    assert answer["ok"] is False
    assert answer["code"] == "slow"
    assert "did not answer" in answer["error"]
    assert "stopped" not in answer["error"]


def test_a_stop_still_reads_as_a_stop():
    """The other half. `release` is what a stopped turn does, and it must keep its own sentence.

    This is why the fix is a third state rather than a different sentinel: both facts are real
    and both need saying, so one field that distinguishes them is the whole of it.
    """
    call = a_call(id="call-stop")

    def stopper():
        for _ in range(500):
            if calls.held(call.id) is not None:
                calls.release("c-1")
                return
            threading.Event().wait(0.002)

    hand = threading.Thread(target=stopper, daemon=True)
    hand.start()
    answer = calls.ask(call)
    hand.join(timeout=2)

    assert answer["code"] == "stopped"
    assert "stopped" in answer["error"]


def test_a_frame_that_keeps_not_answering_is_eventually_given_up_on():
    """`UNRESPONSIVE_AFTER` now reachable from the path that actually happens.

    Before this the counter was only touched by the server's own deadline, which does not fire
    while a renderer is alive to answer first — so the circuit breaker written for a hung frame
    in an open window could not trip while that window was open.
    """
    for n in range(calls.UNRESPONSIVE_AFTER):
        assert answered_with(a_call(id=f"c-{n}"), ok=False)["code"] == "slow"

    given_up = calls.ask(a_call(id="c-last"))

    assert given_up["code"] == "unresponsive"
    assert "stopped answering" in given_up["error"]


def test_one_real_answer_clears_the_count():
    """A frame that comes back is a frame that comes back. The counter is consecutive misses."""
    answered_with(a_call(id="c-a"), ok=False)
    answered_with(a_call(id="c-b"), ok=True, value={"said": "yes"})

    for n in range(calls.UNRESPONSIVE_AFTER):
        assert answered_with(a_call(id=f"c-again-{n}"), ok=False)["code"] == "slow"
    assert calls.ask(a_call(id="c-z"))["code"] == "unresponsive"


def test_the_server_deadline_is_the_outer_one():
    """The two clocks cover different failures, so the outer one cannot be the smaller.

    `min(command timeout, DEADLINE)` made the server's clock fire first or level with the
    renderer's in every case the manifest ceiling allows, which is the one arrangement that
    guarantees the *server* answers for a slow frame while the renderer's answer is in flight.
    The invariant is one line and it is worth pinning: strictly longer than anything a manifest
    can ask for.
    """
    from kith.domain.plugins import TIMEOUT_MS_RANGE

    assert TIMEOUT_MS_RANGE[1] / 1000 < calls.DEADLINE
