"""Scope as a property of the work, not a fact about the plan.

Counted on a real brain before this migration: five of twenty-one tables carried a project —
tasks, conversations, messages, memories, milestones. Everything a task owned was two joins away
and everything he actually *did* had none at all: 695 journal entries, 3,112 file touches, 19
workers, every reminder and every source. A project therefore grouped where work was *described*
and evaporated the moment he started working, which is the wrong way round — a plan is the cheap
half to re-derive.

`deliverables.project_id` is the half that is denormalised on purpose, and it is why this was
worth a migration rather than a query. It is not hypothetical: the design board holds a
deliverable and nothing else, needs a project to anchor the file it writes, and had to block on
fetching the task to learn one — while the thumbnail beside it still passes none. Carried rather
than inferred, neither is writable.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kith.infra.db import migrations
from kith.infra.db import repositories as repo
from kith.kernel import session_context

#: Every table that should now be able to say which project its rows belong to.
DOING = ("journal", "file_touches", "workers", "reminders", "sources", "curiosities", "schedules")


@pytest.fixture
def brain(tmp_path: Path) -> Path:
    place = tmp_path / "agent.db"
    migrations.init(place)
    return place


def _columns(path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


class TestTheColumnExists:
    @pytest.mark.parametrize("table", DOING)
    def test_the_doing_layer_can_name_its_project(self, brain, table):
        assert "project_id" in _columns(brain, table)

    def test_a_deliverable_carries_its_own(self, brain):
        assert "project_id" in _columns(brain, "deliverables")

    def test_checkpoints_deliberately_do_not(self, brain):
        # They are keyed by repository root, and a repository root *is* the project's folder.
        # A project_id there would be a second, weaker spelling of something already exact.
        assert "project_id" not in _columns(brain, "checkpoints")


class TestItIsRecorded:
    def test_a_deliverable_takes_the_project_from_its_task(self, brain):
        project = repo.projects.add_project(brain, "Sadeef One")
        pid = project["id"] if isinstance(project, dict) else project
        task = repo.tasks.add_task(brain, "Design the table", project_id=pid)
        tid = task["id"] if isinstance(task, dict) else task

        made = repo.tasks.add_deliverable(brain, tid, "file", "A", "screens/a.html")
        assert made["project_id"] == pid

    def test_a_deliverable_on_an_unfiled_task_is_not_invented_a_project(self, brain):
        task = repo.tasks.add_task(brain, "Loose end")
        tid = task["id"] if isinstance(task, dict) else task
        made = repo.tasks.add_deliverable(brain, tid, "text", "A", "hello")
        # Nullable on purpose: work outside any project is a real thing.
        assert made["project_id"] is None

    def test_a_journal_entry_takes_the_session_s_project(self, brain):
        project = repo.projects.add_project(brain, "Vigil")
        pid = project["id"] if isinstance(project, dict) else project

        with session_context.working_on(pid):
            entry = repo.journal.add_journal(brain, "Read the spec.")
        assert entry["project_id"] == pid

    def test_a_journal_entry_outside_a_project_has_none(self, brain):
        assert repo.journal.add_journal(brain, "Idle thought.")["project_id"] is None

    def test_an_explicit_project_beats_the_session(self, brain):
        one = repo.projects.add_project(brain, "One")
        two = repo.projects.add_project(brain, "Two")
        a = one["id"] if isinstance(one, dict) else one
        b = two["id"] if isinstance(two, dict) else two

        with session_context.working_on(a):
            entry = repo.journal.add_journal(brain, "Filed elsewhere.", project_id=b)
        assert entry["project_id"] == b


class TestTheBackfill:
    def test_deliverables_that_predate_the_column_are_filled_from_their_task(self, tmp_path):
        """The rows already on disk — 324 of them on the real brain — are not left blank."""
        place = tmp_path / "old.db"
        migrations.init(place)

        project = repo.projects.add_project(place, "Backfilled")
        pid = project["id"] if isinstance(project, dict) else project
        task = repo.tasks.add_task(place, "Old work", project_id=pid)
        tid = task["id"] if isinstance(task, dict) else task

        # A deliverable as it looked before v47: filed against a task, knowing nothing else.
        with sqlite3.connect(place) as conn:
            conn.execute(
                "INSERT INTO deliverables (task_id, kind, title, content, created_at, project_id)"
                " VALUES (?,?,?,?,?,NULL)",
                (tid, "file", "Old", "screens/old.html", "2026-01-01T00:00:00+00:00"),
            )
            # Wind the schema back so `init` runs v47 again, which is how an existing install
            # receives it.
            conn.execute("PRAGMA user_version = 46")
        sqlite3.connect(place).close()

        migrations.init(place)

        with sqlite3.connect(place) as conn:
            filled = conn.execute(
                "SELECT project_id FROM deliverables WHERE content = 'screens/old.html'"
            ).fetchone()[0]
        assert filled == pid
