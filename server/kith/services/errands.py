"""Errands that were sent and have not come back yet.

`delegate_subtask` waits by default, and that is right for what it is mostly used for: the
answer is what the next tool call is *for* — find where the ledger is instantiated, then edit
it — so handing back a receipt instead of a finding just means ending the turn to go and
collect one. But some errands are genuinely long, and holding a whole turn on a scout reading
forty pages of documentation is the same mistake `run_tests` used to make by holding one on a
half-hour test suite.

So `wait=false` sends one and carries on, and this is where the result waits in the meantime.

**Delivered at a round boundary, into the turn that sent it.** Not through the scheduler, which
is the other way this could have been built and is worse for it in two ways that are easy to
measure. `scheduler.wake_finished` is a 30-second timer, so a twenty-second errand would take
up to fifty seconds to be noticed — slower than simply waiting for it. And it opens a *new*
turn under `session_context.nobody_watching()`, so a finding asked for in a conversation you are
sitting in would come back to nobody, in a turn with a different permission gate, that has to
work out from the transcript what it was doing. The round boundary is the same place a steer
lands and for the same reason: it is the only moment the conversation is a list a provider will
accept.

**A turn cannot end with an errand still out.** It is work the turn asked for, so the loop waits
rather than dropping it — see :func:`wait_for_all`. Bounded, and it reads the stop switch, so
this cannot be the thing that makes Stop feel broken.

In memory and per conversation, deliberately, like `services/steering.py`: an errand is only
meaningful to the turn that sent it, and one that outlived a restart would come back to a
conversation with no idea what it was.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

#: The most errands one conversation may have out at once. Six, which is `max_parallel`'s
#: *default* rather than its ceiling — that knob goes to 16 — and the reason to be stricter
#: here is that these are not six searches, they are six live model streams, each with a round
#: budget of its own. A turn that can open twenty is a turn that can open twenty upstream
#: connections and spend twenty budgets on one thought.
MAX_OUT = 6

#: How long the end of a turn will wait for an errand that has not reported. Generous, because
#: the alternative is throwing away work the turn asked for — and it is not a hang: the wait
#: wakes on every delivery and on the stop switch.
WAIT_SECONDS = 180.0


@dataclass
class _Errand:
    id: str
    objective: str
    #: What came back, or None while it is still out.
    answer: dict | None = None
    #: True once the loop has put the answer into the prompt, so it is delivered exactly once.
    taken: bool = False


@dataclass
class _Board:
    errands: dict[str, _Errand] = field(default_factory=dict)


_BOARD: dict[str, _Board] = {}

#: One condition over the whole board. Written from errand threads and read from turn threads,
#: which is two thread pools on one piece of state — and the wait below needs to be woken by a
#: delivery rather than to spin on a sleep.
_STATE = threading.Condition(threading.RLock())


def sent(conversation_id: str, errand_id: str, objective: str) -> bool:
    """Note that an errand is out. False when this conversation already has :data:`MAX_OUT`.

    Refusing here rather than in the tool, because here is the only place that knows how many
    are already out — and the tool would have to ask this to find out.
    """
    with _STATE:
        board = _BOARD.setdefault(conversation_id, _Board())
        if sum(1 for one in board.errands.values() if one.answer is None) >= MAX_OUT:
            return False
        board.errands[errand_id] = _Errand(id=errand_id, objective=objective)
        return True


def deliver(conversation_id: str, errand_id: str, answer: dict) -> None:
    """An errand has come back. Wakes anything waiting on it."""
    with _STATE:
        board = _BOARD.get(conversation_id)
        if board is None or errand_id not in board.errands:
            return
        board.errands[errand_id].answer = answer
        _STATE.notify_all()


def outstanding(conversation_id: str) -> int:
    """How many errands this conversation has sent and not heard back from."""
    with _STATE:
        board = _BOARD.get(conversation_id)
        if board is None:
            return 0
        return sum(1 for one in board.errands.values() if one.answer is None)


def collect(conversation_id: str) -> list[str]:
    """Everything that has come back and not yet been put into the prompt, as prompt text.

    Formatted here rather than in the loop. The loop's business is *when* something enters the
    conversation; what an errand coming back should read like is this module's, and putting it
    there would be the second place that knows what an errand is.
    """
    with _STATE:
        board = _BOARD.get(conversation_id)
        if board is None:
            return []
        ready = [one for one in board.errands.values() if one.answer is not None and not one.taken]
        for one in ready:
            one.taken = True
    return [_as_prompt(one) for one in ready]


def has_ready(conversation_id: str) -> bool:
    """Is there anything back that has not been put into the prompt yet?

    Asked by the loop after it has waited: coming back round for another model call is only
    worth a round if there is something new to show him. Without this a wait that timed out
    would send the same prompt again and burn the rest of the budget on it.
    """
    with _STATE:
        board = _BOARD.get(conversation_id)
        if board is None:
            return False
        return any(one.answer is not None and not one.taken for one in board.errands.values())


def wait_for_all(conversation_id: str, stopped=None, seconds: float | None = None) -> None:
    """Block until nothing is out, the stop switch is set, or the time runs out.

    ``stopped`` is a predicate rather than an event, so the caller decides what stopping means
    and this does not have to know about the kernel. `None` means nothing can interrupt it,
    which is what a test or a script passes.

    ``seconds`` defaults to :data:`WAIT_SECONDS` *read at call time*, not bound as a default
    argument. A default is evaluated once at import, so the constant could not be changed —
    which is not a hypothetical: the test that pins down "a wait that comes back with nothing
    ends rather than spinning" turns it down to a tenth of a second, and against a bound
    default it sat for the full three minutes instead.

    Woken by every delivery, so an errand that comes back in two seconds costs two seconds
    rather than a polling interval — the whole objection to routing this through the scheduler.
    """
    deadline = WAIT_SECONDS if seconds is None else seconds
    step = 0.5
    with _STATE:
        while deadline > 0:
            board = _BOARD.get(conversation_id)
            if board is None or not any(one.answer is None for one in board.errands.values()):
                return
            if stopped is not None and stopped():
                return
            # A short wait rather than one long one, because `stopped` is a flag nothing
            # notifies this condition about — a delivery wakes us, a Stop does not.
            _STATE.wait(step)
            deadline -= step


def abandoned(conversation_id: str, errand_id: str) -> bool:
    """Has the turn that sent this errand gone without it?

    True once :func:`forget` has cleared the board, which happens on every way a turn ends —
    including the one that matters here, a `wait_for_all` that timed out on an errand still
    working. Nothing else tells the errand about that. Its own stop check reads
    `stopping.asked_to_stop`, and the turn's switch is *disarmed* when the turn ends, so from
    the errand's side a finished turn and a healthy one are indistinguishable: it would run out
    its whole round budget, make every model call, get billed for all of them, and hand its
    findings to a board that no longer exists.

    Money spent with nothing to show and no trace of the spending, which is the worst shape a
    leak can have. This is the read that stops it.
    """
    with _STATE:
        board = _BOARD.get(conversation_id)
        return board is None or errand_id not in board.errands


def forget(conversation_id: str) -> None:
    """Clear the board when a turn ends.

    Called by the loop on its way out, on every path. An errand whose answer nobody collected is
    dropped here rather than kept for the next turn: the turn that asked the question is over,
    and an answer arriving in the next one would be a finding with no question in front of it.
    """
    with _STATE:
        _BOARD.pop(conversation_id, None)


def _as_prompt(one: _Errand) -> str:
    """What an errand coming back reads like in the conversation.

    A parenthetical note in the same voice as the rest of the harness's asides — this is the
    harness telling him something, not his person, and the difference matters. Every one of
    these that pretended to be his person saying something is now a bug: `_LANDING_DIRECTIVE`
    and the `delegated` latch both spoke in a voice that was not there and were obeyed as
    instructions rather than read as information.

    This one is only information, and information he asked for.
    """
    answer = one.answer or {}
    body = str(answer.get("findings") or "").strip() or "It came back with nothing."
    tally = str(answer.get("looked_at") or "")
    error = str(answer.get("error") or "")
    lines = [f'(The errand you sent — "{one.objective}" — has come back.)', "", body]
    if tally:
        lines += ["", f"(It looked at: {tally}.)"]
    if error:
        lines += ["", f"(It was cut short: {error}. Treat the above as partial.)"]
    return "\n".join(lines)
