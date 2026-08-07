"""The flight recorder keeps its rows through the rename.

`tick_log` was never tick-specific — `add_tick_log(..., mode="chat", ...)` is called from
`_MindFeed.finish()`, so this table is where a conversation records what it cost, and it is
most of what the money dashboard reads. The name goes with the loop it was named after.
Losing a single row would be the worst available outcome of that, so the migration is the
first thing here that gets tested.
"""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo
from kith.infra.db.connection import apply_migrations, connect
from kith.infra.db.migrations import _migrations


def _at(path: Path, count: int):
    """A database migrated to exactly the first `count` migrations."""
    conn = connect(path)
    apply_migrations(conn, _migrations()[:count])
    return conn


class TestTheRenameCarriesEveryRow:
    def test_rows_written_before_the_rename_are_still_there(self, tmp_path: Path):
        path = tmp_path / "agent.db"
        conn = _at(path, 28)  # everything up to and including v28, before the rename
        try:
            conn.execute(
                "INSERT INTO tick_log (at, mode, focus, tools, tokens_in, tokens_out, seconds, outcome)"
                " VALUES ('2026-01-01T00:00:00', 'chat', 'a task', '[]', 900, 40, 1.0, 'answered')"
            )
            conn.commit()
            apply_migrations(conn, _migrations())  # now v29
            rows = conn.execute("SELECT mode, tokens_in, tokens_out, outcome FROM turn_log").fetchall()
        finally:
            conn.close()
        assert [tuple(r) for r in rows] == [("chat", 900, 40, "answered")]

    def test_the_old_name_is_gone(self, tmp_path: Path):
        path = tmp_path / "agent.db"
        conn = _at(path, len(_migrations()))
        try:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        assert "turn_log" in names
        assert "tick_log" not in names

    def test_a_fresh_database_lands_in_the_same_place(self, tmp_path: Path):
        """The migration has to be right for someone who never had `tick_log` at all."""
        path = tmp_path / "fresh.db"
        conn = _at(path, len(_migrations()))
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(turn_log)")}
        finally:
            conn.close()
        assert {"mode", "tokens_in", "tokens_out", "tokens_uncached", "outcome"} <= cols


class TestTheRepositoryStillRecords:
    def test_a_row_goes_in_and_comes_back(self, db: Path):
        repo.messages.add_turn_log(
            db,
            "2026-01-01T00:00:00",
            "chat",
            "a task",
            ["read_file"],
            900,
            40,
            1.0,
            "answered",
            tokens_uncached=900,
        )
        rows = repo.messages.list_turn_log(db, 10)
        assert len(rows) == 1
        assert (rows[0]["mode"], rows[0]["tokens_out"]) == ("chat", 40)

    def test_the_summary_counts_it(self, db: Path):
        repo.messages.add_turn_log(
            db, "2026-01-01T00:00:00", "chat", None, [], 10, 5, 0.5, "answered", tokens_uncached=10
        )
        assert repo.messages.turn_log_summary(db)["ticks"] == 1
