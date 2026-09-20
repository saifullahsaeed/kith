"""`view_task` folds into `list_tasks`, and opening several is one call.

`view_task(id)` is `list_tasks` narrowed to one row and shown in full — 259 characters of
schema and a separate decision for the model to make, to express what an argument expresses.
Together they were 873 characters for 48 calls.

The same plural rule as everywhere else applies on the way in. Reviewing a milestone means
opening the three tasks under it, and that was three rounds; `ids` makes it one. See
`domain.tooling.many` for why the shape is the fix and the paragraph asking for it was not.

`view_task` keeps working forever through `aliases.RETIRED` — `conversations.full_messages`
replays old tool calls on every resumed turn.
"""

from __future__ import annotations

from kith.infra.db import repositories as repo
from kith.tools import aliases
from kith.tools.registry import all_tools, require


def _task(db, goal="a task"):
    return repo.tasks.add_task(db, goal)["id"]


class TestTheMergedTool:
    def test_view_task_is_gone_and_list_tasks_remains(self):
        names = set(all_tools())
        assert "list_tasks" in names
        assert "view_task" not in names

    def test_listing_still_lists(self, db):
        _task(db, "one")
        _task(db, "two")
        out = require("list_tasks").run(db, {})
        assert len(out["items"]) == 2

    def test_an_id_opens_that_task_in_full(self, db):
        tid = _task(db, "the one I want")
        repo.tasks.add_checklist_item(db, tid, "a step")
        # One id returns the task itself, exactly as `view_task` did — see the handler, and
        # the rule in `domain.tooling.many`: a single-path result stays byte-identical so no
        # renderer needs a branch.
        out = require("list_tasks").run(db, {"ids": tid})
        assert out["goal"] == "the one I want"
        assert out["checklist"], "opening a task must carry its checklist"

    def test_several_ids_open_together(self, db):
        a, b = _task(db, "first"), _task(db, "second")
        out = require("list_tasks").run(db, {"ids": [a, b]})
        assert [t["goal"] for t in out["items"]] == ["first", "second"]

    def test_a_single_missing_id_says_so_the_way_it_always_did(self, db):
        assert require("list_tasks").run(db, {"ids": 9999}) == {"note": "No such task."}

    def test_a_missing_id_says_so_rather_than_vanishing(self, db):
        """Silently returning fewer rows than were asked for is the failure mode here: he asks
        for three and reasons about two without noticing the third was never there."""
        tid = _task(db, "real")
        out = require("list_tasks").run(db, {"ids": [tid, 9999]})
        assert [t["goal"] for t in out["items"]] == ["real"]
        assert 9999 in out.get("missing", [])

    def test_listing_without_ids_stays_a_summary(self, db):
        """A page of tasks must not become a page of full task detail — that is the whole
        reason the two were separate, and the merge must not quietly undo it."""
        tid = _task(db, "one")
        repo.tasks.add_checklist_item(db, tid, "a step")
        out = require("list_tasks").run(db, {})
        assert "checklist" not in out["items"][0]


class TestOldCallsStillOpen:
    def test_view_task_points_at_list_tasks(self):
        assert aliases.RETIRED["view_task"].now == "list_tasks"

    def test_a_replayed_view_still_works(self, db):
        from kith.tools import run_tool

        tid = _task(db, "from the archive")
        # `run_tool` answers in an envelope — the trap commit cfe691b names: "part.result is
        # an envelope, and I read the outside".
        out = run_tool("view_task", {"id": tid}, db)["result"]
        assert out["goal"] == "from the archive"


class TestItIsSmaller:
    def test_the_merged_schema_costs_less_than_the_two_it_replaces(self):
        import json

        assert len(json.dumps(require("list_tasks").schema())) < 873
