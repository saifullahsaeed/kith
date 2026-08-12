"""Eight statuses down to four, and the due date out with them.

The eight were not being used. Counted on the real board the day this was written: 67 done,
4 waiting, 3 planned, 3 dropped, 3 backlog, 1 working — and **nothing had ever been in
`review`**. Two of the eight were dead and two more were indistinguishable in practice, which is
how a status ends up wrong often enough to be worth removing rather than fixing.

What each retired one becomes:

* `backlog` -> `planning`. The distinction was "not started" versus "a plan is being drafted", and
  since every task now lands in the gate there is no state before planning.
* `planned` -> `approved`. A rename to what it actually means: you said yes.
* `waiting` and `review` -> `working`. Both meant "someone else's turn", which is now a **question
  in chat** rather than a column — chat has the conversation the work came out of, and `ask` holds
  the turn until it is answered. `review` deliberately does not become `done`: work he claimed was
  finished and nobody checked is not finished, so the honest state is that he is still on it.

`due_at` goes in the same migration. 77 of 81 tasks carried one, essentially all auto-stamped, and
a field that is always set carries no signal.
"""

from __future__ import annotations

from pathlib import Path

from kith.infra.db.connection import apply_migrations, connect
from kith.infra.db.migrations import _migrations

#: Every status a row could be speaking on the way in, paired with the goal that names it.
BEFORE = (
    ("A parked errand", "backlog"),
    ("A plan being drafted", "planning"),
    ("An approved piece of work", "planned"),
    ("Work in flight", "working"),
    ("Finished, nobody looked", "review"),
    ("Parked on the person", "waiting"),
    ("Finished work", "done"),
    ("Abandoned work", "dropped"),
)


def _legacy_db(path: Path) -> None:
    """A database migrated to just before the four statuses, with rows in all eight.

    The cut is located by name rather than by position — `[:-1]` means "all but the last" only
    until the next migration lands, at which point it silently runs the thing under test as part
    of the setup and then asserts it has not run.
    """
    conn = connect(path)
    try:
        cut = next(i for i, fn in enumerate(_migrations()) if fn.__name__ == "v34_four_task_statuses")
        apply_migrations(conn, _migrations()[:cut])
        now = "2026-08-01T00:00:00Z"
        for goal, status in BEFORE:
            conn.execute(
                "INSERT INTO tasks (goal, status, created_at, updated_at, priority, "
                "description, created_by, due_at) VALUES (?, ?, ?, ?, 'normal', '', 'kith', ?)",
                (goal, status, now, now, "2026-08-09T00:00:00Z"),
            )
        conn.commit()
    finally:
        conn.close()


def _migrated(tmp_path: Path) -> dict[str, str]:
    path = tmp_path / "agent.db"
    _legacy_db(path)
    conn = connect(path)
    try:
        apply_migrations(conn, _migrations())
        rows = conn.execute("SELECT goal, status FROM tasks").fetchall()
    finally:
        conn.close()
    return {row["goal"]: row["status"] for row in rows}


class TestEveryOldStatusLandsSomewhere:
    def test_backlog_becomes_planning(self, tmp_path):
        assert _migrated(tmp_path)["A parked errand"] == "planning"

    def test_planned_becomes_approved(self, tmp_path):
        assert _migrated(tmp_path)["An approved piece of work"] == "approved"

    def test_waiting_becomes_working(self, tmp_path):
        assert _migrated(tmp_path)["Parked on the person"] == "working"

    def test_review_becomes_working_rather_than_done(self, tmp_path):
        # Not `done`. Nobody checked it, and the whole reason `review` existed was that he
        # cannot be the only judge of his own work — closing it here would grant exactly the
        # pass the column was invented to withhold.
        assert _migrated(tmp_path)["Finished, nobody looked"] == "working"

    def test_the_ones_that_survive_are_untouched(self, tmp_path):
        statuses = _migrated(tmp_path)
        assert statuses["A plan being drafted"] == "planning"
        assert statuses["Work in flight"] == "working"
        assert statuses["Finished work"] == "done"
        assert statuses["Abandoned work"] == "dropped"

    def test_nothing_is_left_speaking_a_retired_word(self, tmp_path):
        from kith.domain.enums import TASK_STATUSES

        assert set(_migrated(tmp_path).values()) <= set(TASK_STATUSES)


class TestTheDueDateIsGone:
    def test_the_column_is_dropped(self, tmp_path):
        path = tmp_path / "agent.db"
        _legacy_db(path)
        conn = connect(path)
        try:
            apply_migrations(conn, _migrations())
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
        finally:
            conn.close()
        assert "due_at" not in columns

    def test_the_rows_survive_losing_it(self, tmp_path):
        # A dropped column must not take its rows with it, which is the failure mode of doing
        # this as a table rebuild rather than an ALTER.
        assert len(_migrated(tmp_path)) == len(BEFORE)


def test_it_only_runs_once(tmp_path):
    """What a second `init()` on an already-current database does. A mapping applied twice must
    be a no-op, or `PRAGMA user_version` guarding the second run has a hole in it."""
    path = tmp_path / "agent.db"
    _legacy_db(path)
    conn = connect(path)
    try:
        migrations = _migrations()
        apply_migrations(conn, migrations)
        apply_migrations(conn, migrations)
        rows = conn.execute("SELECT goal, status FROM tasks").fetchall()
    finally:
        conn.close()
    assert {row["goal"]: row["status"] for row in rows} == {
        "A parked errand": "planning",
        "A plan being drafted": "planning",
        "An approved piece of work": "approved",
        "Work in flight": "working",
        "Finished, nobody looked": "working",
        "Parked on the person": "working",
        "Finished work": "done",
        "Abandoned work": "dropped",
    }
