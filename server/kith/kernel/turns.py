"""Which of the turns Kith starts himself run now, and which wait for a slot.

A turn used to start the moment anything asked for one, on a thread of its own, and nothing
counted them. That was fine while the only things asking were the person and a reminder. Then
agents arrived (2026-10-20): the first real run started 24 of them three levels deep at once,
and a restart resumed every working agent in the same second. Nothing between "start a turn"
and the new thread could say "not yet".

This module is that place. A **background** turn — a reminder or finished process waking a
conversation, mail arriving in the agent tree — is submitted here as a :class:`Request`, and
runs when one of ``limit()`` slots is free. The rest wait in order: wakes before agents, then
first come, first served.

**The person's own turns never come through here.** Typing into a chat starts a turn at once,
as it always has. A cap is only worth having on work nobody is sitting in front of, and the
whole point of it is that your message never waits behind a pile of agent turns.

**A turn waiting on someone else gives its slot back** (:func:`parked`). A handler waiting for
its agents, an agent waiting on its handler's answer, a turn waiting on a question or a
permission: none of them is using the model, and a handler holding a slot while the agents it
waits for sit in the queue behind it is a deadlock that looks like a slow tree. Coming back
from a wait does not wait for a slot again — that would be the same deadlock one step later —
so the count can run over the limit for a moment, and new turns hold off until it is back under.

**What a turn is, this module does not know.** It is handed a runner at boot by the composition
root (`app.py`), and a request carries only data plus the context the turn must run in. The
runner lives in the chat route because that is the layer that can reach both the services and
the tool registry; the services that submit work here never have to reach up to it.

In the kernel because every layer submits to it or parks in it — the agent tree and the
scheduler (services), the permission gate (infra), the chat route (api) — and it needs nothing
from any of them.
"""

from __future__ import annotations

import contextlib
import contextvars
import itertools
import threading
import traceback
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from kith.kernel import changes, session_context

#: Queues, in the order they are served. A wake is a reminder or a finished process coming back
#: to the conversation that asked for it, or mail for a chat that is not itself an agent — small,
#: and somebody is usually expecting it. An agent's mail is bulk.
LANES = ("wake", "agents")

#: How many background turns may run when the runner was installed without a limit.
DEFAULT_LIMIT = 4


@dataclass(eq=False)
class Request:
    """One background turn to run: which conversation, why, and the context to run it in.

    Compared by identity: two reminders for the same conversation are two requests.
    """

    conversation_id: str
    #: One of :data:`LANES`.
    lane: str
    #: What the runner should do: ``wake`` (say ``text`` to the conversation) or ``mail`` (open
    #: a turn with the mail waiting for it).
    kind: str
    text: str = ""
    #: The ContextVars the turn starts from. Fresh by default, and that default is the point: the
    #: thread that frees a slot is usually a turn that has just finished, still pinned to its own
    #: worktree, and a turn started inside that context would write there.
    context: contextvars.Context = field(default_factory=contextvars.Context)
    #: How many waits this turn is inside right now. A parked turn does not count against the limit.
    parked: int = 0
    seq: int = 0
    #: ``started`` or ``nothing`` once the runner has been called. Kept on the request because a
    #: turn can finish — and leave the running set — before the caller that submitted it looks.
    outcome: str = ""


Runner = Callable[[Request], object]

_LOCK = threading.Lock()
#: Held for a whole dispatch, so starting one turn finishes before the next is looked at. A runner
#: checks "is a turn already running in this conversation" and acting on the answer has to happen
#: before anyone else asks. Re-entrant because a runner may submit.
_DISPATCH = threading.RLock()
_queue: list[Request] = []
_running: set[Request] = set()
_runner: Runner | None = None
_limit: Callable[[], int] = lambda: DEFAULT_LIMIT  # noqa: E731
_numbers = itertools.count()


def install(runner: Runner, limit: Callable[[], int] | None = None) -> None:
    """Say how a turn is started, and how many may run at once. Anything already waiting starts.

    ``runner`` is called with a request, inside that request's context, and returns something truthy
    when it started a turn — which must call :func:`ended` with the same request when it is over —
    or something falsy when there was nothing to start.
    """
    global _runner, _limit
    with _LOCK:
        _runner = runner
        _limit = limit or (lambda: DEFAULT_LIMIT)
    _dispatch()


def submit(request: Request) -> str:
    """Run a background turn now if a slot is free, else queue it.

    Returns ``started``, ``queued``, or ``nothing`` (the runner found nothing to do). A second
    ``mail`` request for a conversation that already has one queued is the same request: the turn
    takes every letter waiting when it starts.
    """
    with _LOCK:
        if request.kind == "mail" and any(
            one.kind == "mail" and one.conversation_id == request.conversation_id for one in _queue
        ):
            return "queued"
        request.seq = next(_numbers)
        _queue.append(request)
    _changed(request.conversation_id)
    _dispatch()
    with _LOCK:
        return request.outcome or "queued"


def ended(request: Request) -> None:
    """A background turn is over: free its slot and start whatever is next.

    Safe to call for a request this module never started; it does nothing.
    """
    with _LOCK:
        if request not in _running:
            return
        _running.discard(request)
    _dispatch()


@contextlib.contextmanager
def parked() -> Iterator[None]:
    """Around a wait on someone else: the turn's slot is free for as long as it waits.

    Finds the turn by the conversation the calling code is working in, so nothing has to be passed
    down to the wait. Outside a background turn — the person's own, a test, a script — it does
    nothing.
    """
    found = _running_in(session_context.current())
    if found is None:
        yield
        return
    with _LOCK:
        found.parked += 1
        opened = found.parked == 1
    if opened:
        _dispatch()
    try:
        yield
    finally:
        with _LOCK:
            found.parked -= 1


def cancel(conversation_id: str) -> int:
    """Drop every queued turn for a conversation. Returns how many. A running one is not touched."""
    with _LOCK:
        dropped = [one for one in _queue if one.conversation_id == conversation_id]
        for one in dropped:
            _queue.remove(one)
    if dropped:
        _changed(conversation_id)
    return len(dropped)


def queued() -> list[str]:
    """The conversations waiting for a slot, in the order they will get one."""
    with _LOCK:
        return [one.conversation_id for one in sorted(_queue, key=_order)]


def is_queued(conversation_id: str) -> bool:
    with _LOCK:
        return any(one.conversation_id == conversation_id for one in _queue)


def running() -> int:
    """Background turns holding a slot right now — parked ones not counted."""
    with _LOCK:
        return _active()


def reset() -> None:
    """Forget everything: queue, slots, runner. For tests, which share this module's state."""
    global _runner, _limit
    with _LOCK:
        _queue.clear()
        _running.clear()
        _runner = None
        _limit = lambda: DEFAULT_LIMIT  # noqa: E731


def _dispatch() -> None:
    """Start queued turns while there are free slots."""
    with _DISPATCH:
        while True:
            limit = _read_limit()
            with _LOCK:
                if _runner is None or not _queue or _active() >= limit:
                    return
                request = min(_queue, key=_order)
                _queue.remove(request)
                # Holding the slot before the runner is called, not after it returns: the turn it
                # starts runs on its own thread and may finish — and call `ended` — first.
                _running.add(request)
                run = _runner
            try:
                started = request.context.run(run, request)
            except Exception:
                traceback.print_exc()
                started = None
            with _LOCK:
                request.outcome = "started" if started else "nothing"
                if not started:
                    _running.discard(request)
            _changed(request.conversation_id)


def _read_limit() -> int:
    try:
        return max(0, int(_limit()))
    except Exception:
        return DEFAULT_LIMIT


def _active() -> int:
    return sum(1 for one in _running if one.parked == 0)


def _order(request: Request) -> tuple[int, int]:
    lane = LANES.index(request.lane) if request.lane in LANES else len(LANES)
    return lane, request.seq


def _running_in(conversation_id: str) -> Request | None:
    if not conversation_id:
        return None
    with _LOCK:
        return next((one for one in _running if one.conversation_id == conversation_id), None)


def _changed(conversation_id: str) -> None:
    """Queued and not-yet-running is a state the agent map shows. Swallowed, like every notice."""
    try:
        changes.publish("agents", conversation_id)
    except Exception:
        pass
