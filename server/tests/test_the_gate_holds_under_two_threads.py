"""The permission gate is reached from two thread pools at once, and had no lock.

`require_path` and `require_command` run on tool threads — several at a time, because a round's
network-bound calls go out through a `ThreadPoolExecutor` — while `approve`, `deny` and `revoke`
run on Flask request threads. Five pieces of shared state were read-modify-written from both
with nothing in between.

The clearest of them is `_next_id`. `_refuse` does::

    global _next_id
    _next_id += 1
    request = Request(id=f"p{_next_id}", ...)
    _pending[request.id] = request

Two refusals interleaving there get the *same* id, so the second overwrites the first in
`_pending` — and the first turn then waits out its full fifteen-minute deadline for an answer
that can no longer reach it, because the id it is waiting on now belongs to somebody else.

These tests hammer the paths concurrently. They are not proof — a race that does not happen is
not a race that cannot — but the id test reproduces reliably on the unlocked version, which is
enough to keep the lock honest.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from kith.infra import permissions


@pytest.fixture(autouse=True)
def clean_gate():
    """Each test gets the module's state to itself, and gives it back."""
    with permissions._state:
        pending = dict(permissions._pending)
        answered = dict(permissions._answered)
        grants = set(permissions._session_grants)
        permissions._pending.clear()
        permissions._answered.clear()
        permissions._session_grants.clear()
    yield
    with permissions._state:
        permissions._pending.clear()
        permissions._pending.update(pending)
        permissions._answered.clear()
        permissions._answered.update(answered)
        permissions._session_grants.clear()
        permissions._session_grants.update(grants)


class TestTwoRefusalsAtOnce:
    def test_every_request_gets_an_id_of_its_own(self):
        """The one that loses a turn rather than a number.

        `MAX_PENDING` is 12 and eviction drops the oldest, so with more refusals than that the
        count is bounded — what must hold is that no two live requests ever shared an id, which
        is what a duplicate would silently cause.
        """
        made: list[str] = []
        lock = threading.Lock()

        def refuse(n: int) -> None:
            decision = permissions._refuse("write", f"file-{n}", "why", f"sig-{n}")
            with lock:
                made.append(decision.request.id)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(refuse, range(64)))

        assert len(made) == 64
        assert len(set(made)) == 64, (
            f"{64 - len(set(made))} refusals were handed an id another already had — the turn "
            "waiting on the overwritten one can never be answered"
        )

    def test_the_pending_list_never_exceeds_its_cap(self):
        """Eviction is a read-modify-write too: `while len(_pending) > MAX_PENDING: pop`."""
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda n: permissions._refuse("write", f"f{n}", "why", f"s{n}"), range(64)))

        with permissions._state:
            held = len(permissions._pending)
        assert held <= permissions.MAX_PENDING, (
            f"{held} requests are pending against a cap of {permissions.MAX_PENDING}"
        )


class TestAnsweringWhileRefusing:
    def test_one_request_is_answered_exactly_once(self):
        """Two approvals of the same id must not both succeed.

        `approve` pops from `_pending` and records the verdict; if the pop and the record are
        not one step, two callers can both find the request and both count as the answer.
        """
        decision = permissions._refuse("write", "the file", "why", "sig")
        request_id = decision.request.id
        wins, misses = [], []
        lock = threading.Lock()

        def answer() -> None:
            try:
                permissions.approve(request_id, scope="once")
                with lock:
                    wins.append(request_id)
            except KeyError:
                with lock:
                    misses.append(request_id)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: answer(), range(8)))

        assert len(wins) == 1, f"{len(wins)} callers each believed they were the one who answered"
        assert len(misses) == 7

    def test_a_waiting_call_is_released_by_the_thread_that_approves(self):
        """The deadlock this lock could have introduced, asserted directly.

        `_wait_for` blocks on `request.settled` for up to fifteen minutes, and the only thing
        that ends the wait is `approve` — which needs the same lock. Holding it across the wait
        would deadlock the gate against the one thing that can open it, and no existing test
        would have said so, because they answer before waiting.
        """
        decision = permissions._refuse("write", "the file", "why", "sig")
        released = threading.Event()

        def wait() -> None:
            decision.request.settled.wait(timeout=10)
            released.set()

        waiter = threading.Thread(target=wait, daemon=True)
        waiter.start()
        permissions.approve(decision.request.id, scope="once")

        assert released.wait(timeout=5), (
            "the approving thread could not release the waiting one — the lock is held across the wait"
        )
        waiter.join(timeout=5)


class TestGrantsUnderContention:
    def test_reading_grants_while_they_are_being_written_does_not_explode(self):
        """`granted()` iterated `_session_grants` directly; a set mutated mid-iteration raises
        `RuntimeError: Set changed size during iteration` and takes down whatever was asking."""
        stop = threading.Event()
        errors: list[BaseException] = []

        def churn() -> None:
            n = 0
            while not stop.is_set():
                with permissions._state:
                    permissions._session_grants.add(f"path:/tmp/{n}")
                    if n > 40:
                        permissions._session_grants.discard(f"path:/tmp/{n - 40}")
                n += 1
                stop.wait(0.0005)  # not a hot spin; the point is interleaving, not throughput

        def ask() -> None:
            try:
                for _ in range(200):
                    # `granted` only. `snapshot()` reads the settings database, and calling it
                    # in a hot loop made this test 1,600 sqlite reads long — slow enough that
                    # the first version of it looked like a deadlock.
                    permissions.granted("path:/tmp/nothing")
                    permissions._session_snapshot()
            except BaseException as exc:
                errors.append(exc)

        writer = threading.Thread(target=churn, daemon=True)
        writer.start()
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda _: ask(), range(4)))
        finally:
            stop.set()
            writer.join(timeout=5)

        assert not errors, f"reading the gate while it was being written raised {errors[0]!r}"
