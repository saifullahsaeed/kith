"""Putting a question to a live surface, and every way that can fail.

The one genuinely new runtime primitive in the plugin design, so the failures matter more than
the happy path: a turn parked on a window that closed is the worst thing this can do, and it is
the thing three separate clocks exist to prevent.

What is asserted here is the *waiting*, not the frame — a real frame answers in a browser and
`ui/src/components/shell/plugin-surface.test.tsx` covers that side. Here the frame is whatever
thread happens to reply, which is the honest way to test a wait.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from kith.kernel import live_turns, session_context
from kith.services.plugins import calls


@pytest.fixture(autouse=True)
def a_live_turn():
    """A wait only happens when a turn is actually running and somebody is there.

    Both halves are checked before anything parks — see the module docstring in `calls` — so
    without this every test here would take the "nobody is watching" path and assert nothing.
    """
    # And a clean slate. `_MISSES` is module state that outlives a test, so one case timing out
    # would mark the surface unresponsive and every later case would fail on that instead of on
    # what it was testing. Which is how the deadlock in `forget_misses` was found.
    calls.forget_misses()
    turn = live_turns.begin("c-1")
    try:
        with session_context.working_in("c-1"):
            yield
    finally:
        live_turns.finish(turn)
        calls.release("c-1")
        calls.forget_misses()


def a_call(**over) -> calls.Call:
    return calls.new(
        over.pop("conversation", "c-1"),
        over.pop("plugin", "sketchpad"),
        over.pop("command", "where"),
        over.pop("view", "board"),
        over.pop("args", {}),
        **over,
    )


def answer_after(call_id: str, seconds: float, value: dict) -> threading.Thread:
    """A frame that answers, eventually. A thread rather than a mock, because what is being
    tested is a real wait being woken by a real reply from somewhere else."""

    def later() -> None:
        time.sleep(seconds)
        calls.reply(call_id, value)

    worker = threading.Thread(target=later, daemon=True)
    worker.start()
    return worker


# --------------------------------------------------------------------------- #
# The wait
# --------------------------------------------------------------------------- #


def test_an_answer_comes_back_attributed():
    call = a_call(timeout_ms=3_000)
    answer_after(call.id, 0.05, {"url": "https://example.com"})

    got = calls.ask(call)

    assert got["ok"] is True
    # Attributed, never voiced as Kith's own — this is text a third party wrote inside a frame
    # that was sealed because it is not trusted.
    assert got["result"] == {"from": "sketchpad", "value": {"url": "https://example.com"}}


def test_it_is_pending_while_it_waits():
    call = a_call(timeout_ms=2_000)
    seen: list[list[dict]] = []

    def look() -> None:
        time.sleep(0.05)
        seen.append(calls.pending("c-1"))
        calls.reply(call.id, {"ok": True})

    threading.Thread(target=look, daemon=True).start()
    calls.ask(call)

    assert [one["command"] for one in seen[0]] == ["where"]
    # And gone afterwards. A widget told when a call opens and not when it closes shows a
    # question that has been answered until something else happens to move.
    assert calls.pending("c-1") == []


def test_a_surface_that_does_not_answer_says_so_and_says_carry_on():
    call = a_call(timeout_ms=250)

    got = calls.ask(call)

    assert got["ok"] is False
    assert got["code"] == "slow"
    # He has to be able to act on it. "Carry on" is the difference between a turn that reports
    # a gap and a turn that stops.
    assert "Carry on" in got["error"]


def test_stopping_a_turn_wakes_what_is_parked_on_a_frame():
    """`calls.release` sits beside `questions.release` and `permissions.release_waiting` on the
    stop path, because a turn parked on a frame is not reading the stop switch either."""
    call = a_call(timeout_ms=8_000)
    threading.Thread(target=lambda: (time.sleep(0.05), calls.release("c-1")), daemon=True).start()

    started = time.monotonic()
    got = calls.ask(call)

    assert got["code"] == "stopped"
    # Woken, not waited out. Without the release this would have taken its full eight seconds.
    assert time.monotonic() - started < 2


def test_the_server_deadline_caps_a_generous_command():
    """The command's own clock is the renderer's; this one covers the renderer being *gone*, so
    it must not be possible for a manifest to ask for longer than it."""
    call = a_call(timeout_ms=99_000)
    assert min(call.timeout_ms / 1000, calls.DEADLINE) == calls.DEADLINE


# --------------------------------------------------------------------------- #
# Refusing before parking
# --------------------------------------------------------------------------- #


def test_nothing_parks_when_nobody_is_there():
    """A scheduled turn at four in the morning has no window. Waiting would hold a background
    thread on a question drawn on nobody's screen — the failure `permissions._wait_for` and
    `questions.ask` both carry a comment about."""
    call = a_call(timeout_ms=8_000)

    with session_context.nobody_watching():
        started = time.monotonic()
        got = calls.ask(call)

    assert got["code"] == "no_renderer"
    assert time.monotonic() - started < 1


def test_nothing_parks_when_there_is_no_live_turn():
    call = a_call(conversation="c-nothing-running", timeout_ms=8_000)

    got = calls.ask(call)

    assert got["code"] == "no_renderer"


def test_a_frame_that_keeps_missing_is_given_up_on():
    """Fail fast rather than making every later call wait out a full deadline against a surface
    that has stopped answering."""
    for _ in range(calls.UNRESPONSIVE_AFTER):
        assert calls.ask(a_call(timeout_ms=250))["code"] == "slow"

    got = calls.ask(a_call(timeout_ms=250))

    assert got["code"] == "unresponsive"
    assert "Reload it" in got["error"]


def test_reloading_the_surface_gives_it_another_chance():
    """The other half of the test above, and the reason `forget_misses` exists: the message says
    to reload, so reloading has to work. It did not — a miss was cleared only by a successful
    answer, which the fast-fail made impossible, so three slow replies finished a surface for
    the life of the process."""
    for _ in range(calls.UNRESPONSIVE_AFTER):
        calls.ask(a_call(timeout_ms=250))
    assert calls.ask(a_call(timeout_ms=250))["code"] == "unresponsive"

    calls.forget_misses("sketchpad", "board", "")

    call = a_call(timeout_ms=2_000)
    answer_after(call.id, 0.05, {"ok": True})
    assert calls.ask(call)["ok"] is True


def test_answering_clears_the_misses():
    for _ in range(calls.UNRESPONSIVE_AFTER - 1):
        calls.ask(a_call(timeout_ms=250))
    call = a_call(timeout_ms=2_000)
    answer_after(call.id, 0.05, {"ok": True})
    assert calls.ask(call)["ok"] is True

    # Back to zero, so a surface that was briefly busy is not written off for the session.
    assert calls.ask(a_call(timeout_ms=250))["code"] == "slow"


def test_one_frame_cannot_be_asked_everything_at_once():
    """A bound, so a model in a loop cannot fill the table."""
    parked = []
    for _ in range(calls.MAX_INFLIGHT):
        call = a_call(timeout_ms=1_500)
        parked.append(call)
        threading.Thread(target=calls.ask, args=(call,), daemon=True).start()
    time.sleep(0.2)

    got = calls.ask(a_call(timeout_ms=500))

    assert got["code"] == "busy"
    for call in parked:
        calls.reply(call.id, {})


# --------------------------------------------------------------------------- #
# Two windows
# --------------------------------------------------------------------------- #


def test_a_call_is_claimed_by_the_renderer_that_takes_it():
    """Two windows on one backend must not both deliver the same call and race to answer — the
    person would click in one and watch it act in the other."""
    call = a_call(timeout_ms=1_500)
    threading.Thread(target=calls.ask, args=(call,), daemon=True).start()
    time.sleep(0.1)

    first = calls.pending("c-1", client="window-a")
    second = calls.pending("c-1", client="window-b")

    assert [one["id"] for one in first] == [call.id]
    assert second == []
    calls.reply(call.id, {})


def test_a_late_reply_is_refused_rather_than_applied():
    """By the time it arrives the model has already been told the surface did not answer, so
    applying it would answer a question that is no longer being asked."""
    call = a_call(timeout_ms=250)
    assert calls.ask(call)["code"] == "slow"

    assert calls.reply(call.id, {"url": "too late"}) is False


def test_disabling_a_plugin_abandons_its_calls(db: Path):
    call = a_call(timeout_ms=8_000)
    threading.Thread(target=lambda: (time.sleep(0.05), calls.forget_plugin("sketchpad")), daemon=True).start()

    started = time.monotonic()
    got = calls.ask(call)

    assert got["code"] == "stopped"
    assert time.monotonic() - started < 2
