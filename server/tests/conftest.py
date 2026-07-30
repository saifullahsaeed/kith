"""Shared fixtures.

Every test gets a real SQLite database built by the real migrations, in a temp
directory. Not mocks: the things that actually broke this codebase were schema and
transaction behaviour, and a mocked database cannot fail the way those did.
"""

from __future__ import annotations

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
