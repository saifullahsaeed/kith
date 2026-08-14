"""The test suite must not start background threads that outlive the test.

This is a test about the tests, and it earns its place because the mistake it guards was not
merely slow — it pointed a live loop at the real database.

It was written for the autonomy loop, and the loop is gone. The hazard is not. It belonged to
*having a timer at all* rather than to what the timer did: `scheduler.start(lambda cid, trigger: None)` runs a daemon
thread that wakes every thirty seconds for the rest of the process, reads `AGENT_DB_PATH` out
of its module, and can reach `stream_agent` when something is due. A thread started in one
test would go on firing through the next, against whatever database that one had patched in.

The first attempt at containing this stopped the threads in teardown, which is the wrong end
of the problem and actively harmful:

* autouse fixtures set up first, so they tear down *last* — after `monkeypatch` has restored
  `AGENT_DB_PATH`. In that window a still-running timer reads the real `server/data/agent.db`,
  and firing a due reminder is a real network call against the real board;
* measured, nine of thirteen joins hit the full two-second timeout with the thread still
  alive, because it was busy doing exactly that. Four seconds of teardown on every test in the
  file, and a twenty-second suite that did not finish.

So the thread simply never starts. Stopping one you should not have started is the wrong fix.
"""

from __future__ import annotations

import threading

from kith.services import scheduler


class TestNothingStartsATimer:
    def test_start_is_stubbed_by_the_autouse_fixture(self):
        """If this ever fails, the fixture has been renamed or dropped and every test in the
        suite is one `scheduler.start(lambda cid, trigger: None)` away from a live timer on the real database."""
        scheduler.start(lambda cid, trigger: None)
        assert scheduler._thread is None

    def test_importing_the_module_starts_nothing(self):
        assert scheduler._thread is None

    def test_no_kith_thread_survives_this_file(self):
        names = [t.name for t in threading.enumerate() if t.name.startswith("kith-")]
        assert names == [], f"threads left running: {names}"


class TestTheDatabaseGuard:
    def test_no_module_still_points_at_the_shipped_database(self, never_the_real_database):
        """The other half of the same guarantee: even with no timer, a test that forgets to
        patch `AGENT_DB_PATH` must not reach `server/data/agent.db`."""
        import sys

        shipped = []
        for name, module in list(sys.modules.items()):
            if not name.startswith("kith."):
                continue
            value = getattr(module, "AGENT_DB_PATH", None)
            if value is not None and "server/data/agent.db" in str(value):
                shipped.append(name)
        assert shipped == [], f"still pointed at the real database: {shipped}"

    def test_the_fixture_hands_back_the_path_it_installed(self, never_the_real_database):
        assert "server/data" not in str(never_the_real_database)
