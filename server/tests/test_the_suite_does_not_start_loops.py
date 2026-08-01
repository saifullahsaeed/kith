"""The test suite must not start background threads that outlive the test.

This is a test about the tests, and it earns its place because the mistake it guards was not
merely slow — it pointed a live loop at the real database.

`keep_working` calls `ensure_loop`, which starts a daemon thread that wakes every second for
the rest of the process. The first attempt to contain that stopped the threads in teardown,
which is the wrong end of the problem and actively harmful:

* autouse fixtures set up first, so they tear down *last* — after `monkeypatch` has restored
  `AGENT_DB_PATH`. In that window a still-running loop reads the real `server/data/agent.db`,
  and `_tick` can reach `stream_agent`, which is a real network call against the real board;
* measured, nine of thirteen joins hit the full two-second timeout with the thread still
  alive, because it was busy doing exactly that. Four seconds of teardown on every test in
  the file, and a twenty-second suite that stopped finishing at all.

So the loop does not start. These assertions keep it that way, and keep the opt-in escape
hatch honest — an untested fixture that claims to start a thread and starts nothing is worse
than no fixture.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from kith.services import conversations

MODULE = sys.modules["kith.autonomy.runner"]


def live_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "kith-autonomy"]


class TestByDefaultNothingRuns:
    def test_keep_working_starts_no_thread(self, db: Path, monkeypatch):
        monkeypatch.setattr(MODULE, "AGENT_DB_PATH", db)
        before = len(live_threads())

        runner = MODULE.AutonomyRunner()
        runner.keep_working(conversations.start(db, "some work")["id"])

        assert len(live_threads()) == before, "a test started a background loop"

    def test_but_it_still_records_the_session_as_working(self, db: Path, monkeypatch):
        """Neutering the thread must not neuter what the tests are actually checking."""
        from kith.infra.db import repositories as repo

        monkeypatch.setattr(MODULE, "AGENT_DB_PATH", db)
        session = conversations.start(db, "some work")["id"]
        MODULE.AutonomyRunner().keep_working(session)

        assert repo.conversations.is_working(db, session) is True

    def test_ensure_loop_is_the_thing_that_is_stubbed(self):
        """Named explicitly so that if the runner stops going through `ensure_loop`, this
        fails rather than the guard silently doing nothing."""
        assert MODULE.AutonomyRunner.ensure_loop.__name__ == "<lambda>"

    def test_no_thread_survives_this_file(self):
        assert live_threads() == []


class TestTheEscapeHatchActuallyWorks:
    def test_it_really_starts_one(self, db: Path, monkeypatch, live_autonomy_loop):
        monkeypatch.setattr(MODULE, "AGENT_DB_PATH", db)
        runner = MODULE.AutonomyRunner()
        runner.ensure_loop()

        assert runner._thread is not None and runner._thread.is_alive(), (
            "live_autonomy_loop wrapped the no-op instead of the real function"
        )
        assert live_autonomy_loop == [runner], "the fixture did not record the runner to stop"

    def test_and_the_thread_is_gone_afterwards(self):
        """Runs after the test above, in the same file. If the fixture's teardown did not
        join, the thread is still here."""
        assert live_threads() == []

    def test_the_loop_it_starts_is_the_real_one(self, db: Path, monkeypatch, live_autonomy_loop):
        """Not just any thread — one that actually ticks. A wrapper that started a thread
        doing nothing would pass the test above and prove nothing."""
        monkeypatch.setattr(MODULE, "AGENT_DB_PATH", db)
        runner = MODULE.AutonomyRunner()
        seen: list[bool] = []
        monkeypatch.setattr(runner, "_has_due", lambda: seen.append(True) or False)
        monkeypatch.setattr(runner, "_sessions_working", lambda: False)

        runner.ensure_loop()
        deadline = time.monotonic() + 3
        while not seen and time.monotonic() < deadline:
            time.sleep(0.05)

        assert seen, "the thread started but the loop never ran a cycle"


class TestTheRealDatabaseIsOutOfReach:
    """The suite was running real agent turns against the real board.

    `test_a_new_step_clears_a_stale_cancel` called `_safe_tick(forced=True)` with no temp
    database and no stubbed model, so it picked the top of the real board and worked it. The
    real tick log recorded 759,681 prompt tokens for one such run, and a file written into
    the workspace. It was also a 145-second test, which is how it was noticed at all.
    """

    def test_no_module_still_points_at_the_shipped_database(self):
        from kith.settings import SERVER_ROOT

        real = SERVER_ROOT / "data" / "agent.db"
        pointing = [
            name
            for name, module in sys.modules.items()
            if name.startswith("kith.") and getattr(module, "AGENT_DB_PATH", None) == real
        ]
        assert not pointing, (
            f"these modules would write to the real database during a test: {pointing}. "
            "Thirteen modules copy the value with `from kith.config import AGENT_DB_PATH`, "
            "so each one needs its own patch."
        )

    def test_a_forced_tick_touches_only_the_temp_database(self, never_the_real_database):
        """The exact call that was working task #110. It must now find an empty board."""
        runner = MODULE.AutonomyRunner()
        runner._safe_tick(forced=True)

        from kith.infra.db import repositories as repo

        assert repo.messages.list_tick_log(never_the_real_database, 5) == [], (
            "a forced tick wrote a row — to which database?"
        )

    def test_the_fixture_hands_back_the_path_it_installed(self, never_the_real_database):
        import kith.autonomy.runner as _r

        assert never_the_real_database == sys.modules["kith.autonomy.runner"].AGENT_DB_PATH
        assert _r is not None
