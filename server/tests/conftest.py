"""Shared fixtures.

Every test gets a real SQLite database built by the real migrations, in a temp
directory. Not mocks: the things that actually broke this codebase were schema and
transaction behaviour, and a mocked database cannot fail the way those did.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import kith.autonomy.runner  # noqa: F401  — imported for the side effect of registering the module
from kith.infra.db import config_store
from kith.infra.db.migrations import init

#: The real `ensure_loop`, captured once at import — before any fixture has replaced it.
#:
#: `kith.autonomy.runner` as an *attribute* of its package is the singleton instance, not the
#: module, so the class is only reachable through sys.modules. And it has to be grabbed here
#: rather than inside a fixture: by the time the opt-in fixture below runs, the autouse one
#: has already put a no-op on the class, and wrapping *that* would produce a fixture that
#: silently starts nothing.
_RUNNER_MODULE = sys.modules["kith.autonomy.runner"]
_REAL_ENSURE_LOOP = _RUNNER_MODULE.AutonomyRunner.ensure_loop


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """A fresh, fully-migrated agent database."""
    path = tmp_path / "agent.db"
    init(path)
    return path


@pytest.fixture
def config_db(tmp_path: Path) -> Path:
    """A fresh config database, seeded exactly as a first run would be."""
    path = tmp_path / "config.db"
    config_store.init(path)
    return path


@pytest.fixture(autouse=True)
def never_the_real_database(tmp_path_factory, monkeypatch):
    """No test can reach `server/data/agent.db`, whatever it forgets to patch.

    This is not hygiene. The suite was running real agent turns against the real board, and
    the real tick log has the receipts:

        09:36:37  start  27 tools  759,681 tokens in   "Completed the inspection step for task #110."
        09:25:06  start  12 tools  265,822 tokens in   "Created work/task-110.md and added a four-s…"

    `test_a_new_step_clears_a_stale_cancel` calls `runner._safe_tick(forced=True)` with no
    temp database and no stubbed model. It therefore picked the top of the *real* board and
    worked it: a million prompt tokens of somebody's money per suite run, files written into
    the workspace, and task #110 advanced by a test asserting something about a cancel flag.
    It also explains a 145-second test.

    Chasing the call sites is the wrong fix — `test_stopping_a_step.py` alone builds six
    runners and patches the path once, and the next one written will forget too. Thirteen
    modules do `from kith.config import AGENT_DB_PATH`, which copies the value, so patching
    `kith.config` alone does nothing; every holder gets its own patch. A test that wants its
    own database still monkeypatches over this, which is what every existing one already does.
    """
    safe = tmp_path_factory.mktemp("never-real") / "agent.db"
    init(safe)
    for name, module in list(sys.modules.items()):
        if name.startswith("kith.") and hasattr(module, "AGENT_DB_PATH"):
            monkeypatch.setattr(module, "AGENT_DB_PATH", safe, raising=False)
    yield safe


@pytest.fixture(autouse=True)
def no_stray_autonomy_loops(monkeypatch):
    """No test starts a real background loop unless it asks for one. None of them ask.

    `keep_working` calls `ensure_loop`, which starts a daemon thread that wakes every second
    for the rest of the process. Nothing stopped them, so a runner built in one test went on
    ticking through the next — reading `AGENT_DB_PATH` out of the module, which the following
    test monkeypatches at its own temp database. It duly began ticking someone else's
    fixtures, which is how it was found: a test expecting two calls saw six, four of them
    from a runner two files away.

    The first attempt at this stopped the threads in teardown, and that was wrong in a way
    worth recording, because it made things *worse* rather than merely slow:

    * Autouse fixtures are set up first, so they tear down LAST — after `monkeypatch` has
      already restored `AGENT_DB_PATH`. In that window a still-running loop is pointed at
      the real `server/data/agent.db`, and `_tick` can reach `stream_agent`, which is a
      real network call against the real board.
    * Measured: nine of thirteen joins hit the full two-second timeout with the thread still
      alive — because it was busy doing exactly that. It cost 4 seconds of teardown on every
      test in the file, and turned a twenty-second suite into one that did not finish.

    Stopping a thread you should never have started is the wrong end of the problem. Nothing
    in the suite asserts the thread exists; every test that calls `keep_working` is checking
    the database flag, not the loop. So the loop simply does not start. A test that genuinely
    wants one can undo this with the `live_autonomy_loop` fixture below.
    """
    monkeypatch.setattr(_RUNNER_MODULE.AutonomyRunner, "ensure_loop", lambda self: None)
    yield


@pytest.fixture
def live_autonomy_loop(monkeypatch):
    """Opt back in to a real background thread, and guarantee it is stopped.

    Calls the function captured at import rather than whatever is on the class now, because
    what is on the class now is the autouse no-op — wrapping that would give you a fixture
    that looks like it starts a loop and starts nothing.

    Every runner that starts one is remembered and stopped here, inside the *test's* own
    fixture stack. That ordering is the point: it unwinds before any `monkeypatch` of
    AGENT_DB_PATH does, so a loop can never be left running against the real database.
    """
    started: list = []

    def remember(self):
        _REAL_ENSURE_LOOP(self)
        started.append(self)

    monkeypatch.setattr(_RUNNER_MODULE.AutonomyRunner, "ensure_loop", remember)
    yield started
    for runner in started:
        runner._stop.set()
        thread = runner._thread
        if thread is not None:
            thread.join(timeout=5)


@pytest.fixture(autouse=True)
def isolated_tuning(tmp_path_factory):
    """Every test resolves tunables against its own empty database.

    Without this, the loop-detection and agent-loop tests would read whichever values
    the developer running them happens to have saved — so a thoughtful change to, say,
    the stall threshold would break the suite on one machine and not another.
    """
    from kith.infra.db import config_store
    from kith.services import tuning

    path = tmp_path_factory.mktemp("tuning") / "config.db"
    config_store.init(path)
    tuning.use_database(path)
    yield path
    tuning.use_database(None)
