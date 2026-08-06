"""Old task statuses carried forward into the planning gate, not left behind.

`v28_task_planning_statuses` is the same shape `v14_tasks_as_issues` used for 'open' -> 'todo':
old vocabulary out, existing rows rewritten rather than left speaking a word the app no longer
recognises. 'todo' had no direct equivalent under the gate (nothing is actionable until a plan
for it is approved), so existing rows are grandfathered straight into 'planned' rather than
sent back to 'planning' — the gate is for work entered from here on, not a plan retroactively
demanded of a queue that was never asked to write one. 'doing' -> 'working' is a plain rename.
"""

from __future__ import annotations

from pathlib import Path

from kith.infra.db.connection import apply_migrations, connect
from kith.infra.db.migrations import _migrations


def _legacy_db(path: Path) -> None:
    """A database migrated up through v27 — one short of the gate — with rows still
    speaking the old 'todo'/'doing' vocabulary, the way a real install arrives at v28."""
    conn = connect(path)
    try:
        apply_migrations(conn, _migrations()[:-1])
        now = "2026-08-01T00:00:00Z"
        for goal, status in (
            ("An old actionable errand", "todo"),
            ("Old work in flight", "doing"),
            ("An old parked errand", "backlog"),
            ("Old finished work", "done"),
        ):
            conn.execute(
                "INSERT INTO tasks (goal, status, created_at, updated_at, priority, "
                "description, created_by) VALUES (?, ?, ?, ?, 'normal', '', 'kith')",
                (goal, status, now, now),
            )
        conn.commit()
    finally:
        conn.close()


def _statuses_by_goal(path: Path) -> dict[str, str]:
    conn = connect(path)
    try:
        rows = conn.execute("SELECT goal, status FROM tasks").fetchall()
    finally:
        conn.close()
    return {row["goal"]: row["status"] for row in rows}


def test_todo_is_grandfathered_into_planned(tmp_path):
    path = tmp_path / "agent.db"
    _legacy_db(path)

    conn = connect(path)
    try:
        apply_migrations(conn, _migrations())
    finally:
        conn.close()

    assert _statuses_by_goal(path)["An old actionable errand"] == "planned"


def test_doing_is_renamed_to_working(tmp_path):
    path = tmp_path / "agent.db"
    _legacy_db(path)

    conn = connect(path)
    try:
        apply_migrations(conn, _migrations())
    finally:
        conn.close()

    assert _statuses_by_goal(path)["Old work in flight"] == "working"


def test_everything_else_is_left_alone(tmp_path):
    path = tmp_path / "agent.db"
    _legacy_db(path)

    conn = connect(path)
    try:
        apply_migrations(conn, _migrations())
    finally:
        conn.close()

    statuses = _statuses_by_goal(path)
    assert statuses["An old parked errand"] == "backlog"
    assert statuses["Old finished work"] == "done"


def test_it_only_runs_once(tmp_path):
    """Applying every migration twice is what a second `init()` on an already-current
    database does — it must not re-touch rows that were never 'todo' or 'doing' to begin
    with, or `PRAGMA user_version` guarding against a second run has a hole in it."""
    path = tmp_path / "agent.db"
    _legacy_db(path)

    conn = connect(path)
    try:
        migrations = _migrations()
        apply_migrations(conn, migrations)
        apply_migrations(conn, migrations)
    finally:
        conn.close()

    assert _statuses_by_goal(path) == {
        "An old actionable errand": "planned",
        "Old work in flight": "working",
        "An old parked errand": "backlog",
        "Old finished work": "done",
    }
