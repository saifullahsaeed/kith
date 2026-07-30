"""How a repository talks to the database: one short transaction per call."""

from __future__ import annotations

from pathlib import Path

from kith.infra.db.connection import connect


class transaction:
    """Context manager: open a connection, commit on success, always close."""

    def __init__(self, path: Path):
        self._conn = connect(path)

    def __enter__(self):
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self._conn.commit()
        finally:
            self._conn.close()
        return False
