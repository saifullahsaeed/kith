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
