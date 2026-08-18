"""Three schema changes for a board two people can share, tested the way the others are.

`v38` drops `notes` and `people`, `v39` adds who filed a task, `v40` gives it a name that
survives leaving the machine. Written together because they are one change to what a task *is*,
and tested together for the same reason — the interesting assertions are about a database that
has been through all three, which is what every real one will be.

The cut is located by name, not by position: `[:-3]` means "all but the last three" only until
the next migration lands, at which point the setup silently runs the thing under test and then
asserts it has not run.
"""

from __future__ import annotations

from pathlib import Path

from kith.domain import keys
from kith.infra.db.connection import apply_migrations, connect
from kith.infra.db.migrations import _migrations

#: Tasks as they were before any of this, with the two tables that are about to go.
BEFORE = (
    ("Wire the routes", "2026-08-01T09:00:00+00:00"),
    ("Build the shell", "2026-08-01T09:00:00+00:00"),  # same instant as the one above
    ("Ship it", "2026-08-14T22:31:07.123456+00:00"),
)


def _legacy_db(path: Path) -> None:
    conn = connect(path)
    try:
        cut = next(i for i, fn in enumerate(_migrations()) if fn.__name__ == "v38_no_notes_or_people")
        apply_migrations(conn, _migrations()[:cut])
        for goal, made in BEFORE:
            conn.execute(
                "INSERT INTO tasks (goal, status, created_at, updated_at, priority, "
                "description, created_by) VALUES (?, 'working', ?, ?, 'normal', '', 'kith')",
                (goal, made, made),
            )
        conn.execute("INSERT INTO notes (title, body, created_at, updated_at) VALUES ('n','b','x','x')")
        conn.execute(
            "INSERT INTO people (name, relationship, profile, created_at, updated_at) "
            "VALUES ('someone','','','x','x')"
        )
        conn.commit()
    finally:
        conn.close()


def _migrated(tmp_path: Path, name: str = "agent.db"):
    path = tmp_path / name
    _legacy_db(path)
    conn = connect(path)
    apply_migrations(conn, _migrations())
    return conn


class TestTheTwoTablesGo:
    def test_they_are_dropped_rather_than_left_empty(self, tmp_path: Path):
        """An unused table is a thing the next person has to work out the status of."""
        conn = _migrated(tmp_path)
        try:
            left = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('notes','people')"
            ).fetchall()
            assert left == []
        finally:
            conn.close()

    def test_the_tasks_beside_them_are_untouched(self, tmp_path: Path):
        conn = _migrated(tmp_path)
        try:
            goals = {row["goal"] for row in conn.execute("SELECT goal FROM tasks")}
            assert goals == {goal for goal, _ in BEFORE}
        finally:
            conn.close()


class TestWhoFiledIt:
    def test_the_column_arrives_blank(self, tmp_path: Path):
        """Deliberately not backfilled. An account is a claim about who somebody was, and
        today's git identity is a guess about who was sitting here in August — a guess
        indistinguishable from a record is worse than a gap that is obviously a gap."""
        conn = _migrated(tmp_path)
        try:
            accounts = {row["account"] for row in conn.execute("SELECT account FROM tasks")}
            assert accounts == {""}
        finally:
            conn.close()


class TestTheNameThatLeavesTheMachine:
    def test_every_existing_task_gets_one(self, tmp_path: Path):
        conn = _migrated(tmp_path)
        try:
            rows = conn.execute("SELECT key FROM tasks").fetchall()
            assert all(keys.looks_like_a_key(row["key"]) for row in rows), [r["key"] for r in rows]
        finally:
            conn.close()

    def test_two_made_in_the_same_instant_still_differ(self, tmp_path: Path):
        """The backfill is derived from the creation time, and two of these share one to the
        microsecond. The id goes in the low bits precisely so that they cannot collide."""
        conn = _migrated(tmp_path)
        try:
            found = [row["key"] for row in conn.execute("SELECT key FROM tasks")]
            assert len(set(found)) == len(found)
        finally:
            conn.close()

    def test_they_sort_by_when_the_task_was_made(self, tmp_path: Path):
        """What makes a directory of briefs readable, since the key becomes the filename."""
        conn = _migrated(tmp_path)
        try:
            rows = conn.execute("SELECT goal, key FROM tasks ORDER BY key").fetchall()
            assert [r["goal"] for r in rows][-1] == "Ship it"
        finally:
            conn.close()

    def test_the_same_history_migrated_twice_produces_the_same_keys(self, tmp_path: Path):
        """Deterministic on purpose. Random keys here would mean a copy of one database becomes
        a second history — the same tasks, unrecognisable to each other, which is the exact
        failure keys exist to prevent."""
        first = _migrated(tmp_path, "one.db")
        second = _migrated(tmp_path, "two.db")
        try:
            a = {r["goal"]: r["key"] for r in first.execute("SELECT goal, key FROM tasks")}
            b = {r["goal"]: r["key"] for r in second.execute("SELECT goal, key FROM tasks")}
            assert a == b
        finally:
            first.close()
            second.close()

    def test_no_two_tasks_may_share_one(self, tmp_path: Path):
        """A unique index, not a convention. A collision is two different tasks quietly
        becoming one across two machines."""
        conn = _migrated(tmp_path)
        try:
            existing = conn.execute("SELECT key FROM tasks LIMIT 1").fetchone()["key"]
            try:
                conn.execute(
                    "INSERT INTO tasks (goal, status, created_at, updated_at, priority, "
                    "description, created_by, account, key) "
                    "VALUES ('dupe','working','x','x','normal','','kith','',?)",
                    (existing,),
                )
                raise AssertionError("a duplicate key was accepted")
            except Exception as refused:
                assert "UNIQUE" in str(refused).upper()
        finally:
            conn.close()


class TestRunningThemTwice:
    def test_a_database_already_migrated_is_left_alone(self, tmp_path: Path):
        """Applied by position past `PRAGMA user_version`, so a second pass must be a no-op.
        The one that would not be is `v40`, which writes rows rather than only altering."""
        conn = _migrated(tmp_path)
        try:
            before = {r["goal"]: r["key"] for r in conn.execute("SELECT goal, key FROM tasks")}
            apply_migrations(conn, _migrations())
            after = {r["goal"]: r["key"] for r in conn.execute("SELECT goal, key FROM tasks")}
            assert before == after
        finally:
            conn.close()
