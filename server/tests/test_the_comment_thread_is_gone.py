"""Progress notes stop being a thing he posts at you.

620 comments on the real board, every one of them raising a notification. The Alerts panel was a
wall of "New note on 'Restore CI compatibility after the domain import migration' (task #79):
Checked remote status…" — four of them inside twenty minutes on one task — and the one message that
actually wanted an answer was somewhere underneath.

The kinds of thing it was used for each have a better home, and all of them already exist:

* **narrating progress** — the task's working file, `.kith/work/task-<id>.md`, which is read at the
  start of every turn and is where the plan already lives;
* **"I need an answer to continue"** — `ask`, which holds the turn until it is answered, in the
  conversation that has the context rather than on a card somebody has to notice;
* **evidence that it is done** — the checklist and the deliverables.

The 620 rows are not deleted. `task_comments` stays in the schema, unread: "get rid of that feature"
is not "destroy the history", and dropping the table later is one migration. What goes is every way
to write a new one and every surface that displayed them.
"""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo
from kith.tools import registry


class TestTheToolsAreGone:
    def test_he_cannot_comment_on_a_task(self):
        assert registry.get("comment_on_task") is None

    def test_he_cannot_ask_on_a_task(self):
        """Asking is `ask`, in chat, which waits. `ask_on_task` parked the question on a card and
        moved the task to a column that no longer exists."""
        assert registry.get("ask_on_task") is None

    def test_ask_is_still_there(self):
        assert registry.get("ask") is not None

    def test_neither_is_offered_in_any_toolset(self, db: Path):
        """A tool named in a prompt but missing from the registry is a round spent on a refusal."""
        from kith.tools import tool_schemas

        offered = {s["function"]["name"] for s in tool_schemas(db)}
        assert "comment_on_task" not in offered
        assert "ask_on_task" not in offered
        assert "ask" in offered


class TestTheTaskNoLongerCarriesThem:
    def test_task_detail_has_no_comments(self, db: Path):
        task = repo.tasks.add_task(db, "Do it")
        detail = repo.tasks.task_detail(db, int(task["id"]))
        assert "comments" not in detail

    def test_it_still_carries_what_replaced_them(self, db: Path):
        task = repo.tasks.add_task(db, "Do it")
        detail = repo.tasks.task_detail(db, int(task["id"]))
        for kept in ("plan", "checklist", "deliverables"):
            assert kept in detail


class TestTheHistoryIsKept:
    def test_the_table_still_exists(self, db: Path):
        """Removing the feature is not deleting what was written with it."""
        from kith.infra.db.connection import connect

        conn = connect(db)
        try:
            names = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        assert "task_comments" in names
