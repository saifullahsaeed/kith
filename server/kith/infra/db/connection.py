"""SQLite connection + migration helpers, shared by Kith's databases.

Schema versioning uses SQLite's built-in ``PRAGMA user_version``: migration at
index *i* upgrades the schema from version *i* to *i+1*, so evolving a schema is
just appending a function to the list.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

Migration = Callable[[sqlite3.Connection], None]


def connect(path: Path) -> sqlite3.Connection:
    """Open a configured connection, creating the parent directory if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def apply_migrations(conn: sqlite3.Connection, migrations: list[Migration]) -> int:
    """Run any migrations past the database's current version. Returns the new
    schema version."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for index in range(version, len(migrations)):
        migrations[index](conn)
        conn.execute(f"PRAGMA user_version = {index + 1}")
    conn.commit()
    return len(migrations)
