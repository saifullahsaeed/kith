"""Shared fixtures.

Every test gets a real SQLite database built by the real migrations, in a temp
directory. Not mocks: the things that actually broke this codebase were schema and
transaction behaviour, and a mocked database cannot fail the way those did.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from kith.infra.db import config_store
from kith.infra.db.migrations import init


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
def no_stray_autonomy_loops():
    """Stop every autonomy loop a test started, before the next test runs.

    `keep_working` calls `ensure_loop`, which starts a daemon thread that lives until the
    process exits. Nothing stopped them, so a runner built in one test kept waking every
    second for the rest of the session — reading `AGENT_DB_PATH` out of the module, which
    the *next* test then monkeypatches at its own temp database. The thread duly began
    ticking someone else's fixtures.

    That is exactly how it was found: a test asserting a tick names its session saw six
    calls where two were expected, four of them from a runner two files away. Silent up to
    that point, because a stray tick against an empty board does nothing observable.

    The runner is reached through the bound method the thread was given. Introspective, and
    confined to here on purpose: the alternative is a registry in production code that
    exists only for tests.
    """
    yield
    for thread in threading.enumerate():
        if thread.name != "kith-autonomy":
            continue
        owner = getattr(getattr(thread, "_target", None), "__self__", None)
        stop = getattr(owner, "_stop", None)
        if stop is not None:
            stop.set()
        thread.join(timeout=2)


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
