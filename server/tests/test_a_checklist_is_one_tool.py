"""`add_checklist_item` and `check_item` become `checklist`.

Two schemas, 747 characters, 328 calls between them in nineteen days, differing by one
boolean. `aliases.RETIRED` already carries the argument for this in its own docstring —
"sixteen schemas were being paid for on every round to offer choices that were parameters" —
and adding a step to a list versus ticking one off is exactly that kind of choice.

**Plural while we are here, and that is the larger half.** Measured across 11,291 rounds,
78-96% of tool-calling rounds called exactly one tool, and the persona paragraph written to fix
it moved the number the wrong way (77% single-call before, 88% after). What worked was changing
the shape: `read_file` takes `paths`, `grep` takes `patterns`. A checklist is the most
one-at-a-time thing Kith does — 165 adds and 163 ticks, essentially all singletons — so it
takes lists, and writing five steps or ticking three off is one round.

**Both old spellings keep working forever.** `conversations.full_messages` replays old tool
calls on every resumed turn, so a conversation from last week must still open.
"""

from __future__ import annotations

from kith.infra.db import repositories as repo
from kith.tools import aliases
from kith.tools.registry import all_tools, require


def _task(db):
    return repo.tasks.add_task(db, "a task with steps")["id"]


def _items(db, task_id):
    return repo.tasks.list_checklist(db, task_id)


class TestTheToolItself:
    def test_the_two_old_tools_are_gone_and_one_replaces_them(self):
        names = set(all_tools())
        assert "checklist" in names
        assert "add_checklist_item" not in names
        assert "check_item" not in names

    def test_adding_several_steps_is_one_call(self, db):
        tid = _task(db)
        require("checklist").run(db, {"id": tid, "add": ["read the spec", "write the test", "ship"]})
        assert [i["text"] for i in _items(db, tid)] == ["read the spec", "write the test", "ship"]

    def test_a_bare_string_still_adds_one(self, db):
        """Models send a bare string into an array parameter. `domain.tooling.many` exists
        because they do."""
        tid = _task(db)
        require("checklist").run(db, {"id": tid, "add": "just the one"})
        assert [i["text"] for i in _items(db, tid)] == ["just the one"]

    def test_ticking_several_off_is_one_call(self, db):
        tid = _task(db)
        require("checklist").run(db, {"id": tid, "add": ["a", "b", "c"]})
        ids = [i["id"] for i in _items(db, tid)]
        require("checklist").run(db, {"items": ids[:2]})
        assert [i["done"] for i in _items(db, tid)] == [1, 1, 0] or [
            bool(i["done"]) for i in _items(db, tid)
        ] == [True, True, False]

    def test_unticking_is_the_same_call_with_done_false(self, db):
        tid = _task(db)
        require("checklist").run(db, {"id": tid, "add": "a"})
        one = _items(db, tid)[0]["id"]
        require("checklist").run(db, {"items": one})
        require("checklist").run(db, {"items": one, "done": False})
        assert not _items(db, tid)[0]["done"]

    def test_adding_and_ticking_in_the_same_call(self, db):
        """The point of the merge: finishing one step and writing the next is one round."""
        tid = _task(db)
        require("checklist").run(db, {"id": tid, "add": "first"})
        one = _items(db, tid)[0]["id"]
        require("checklist").run(db, {"id": tid, "items": one, "add": "second"})
        rows = _items(db, tid)
        assert [r["text"] for r in rows] == ["first", "second"]
        assert rows[0]["done"] and not rows[1]["done"]


class TestASinglePathLooksLikeItAlwaysDid:
    """Same rule `list_tasks` follows and `domain.tooling.many` states: adding one step or
    ticking one item must return exactly what `add_checklist_item` and `check_item` returned,
    because the interface picks a card by sniffing the result's shape, not the tool's name."""

    def test_adding_one_returns_the_row(self, db):
        tid = _task(db)
        out = require("checklist").run(db, {"id": tid, "add": "one step"})
        assert out["text"] == "one step"

    def test_ticking_one_returns_the_row(self, db):
        tid = _task(db)
        require("checklist").run(db, {"id": tid, "add": "a"})
        one = _items(db, tid)[0]["id"]
        out = require("checklist").run(db, {"items": one})
        assert out["id"] == one and out["done"]


class TestOldCallsStillOpen:
    def test_both_retired_names_point_at_it(self):
        assert aliases.RETIRED["add_checklist_item"].now == "checklist"
        assert aliases.RETIRED["check_item"].now == "checklist"

    def test_a_replayed_add_still_works(self, db):
        """`add_checklist_item(id=…, text="…")` as it sits in a week-old transcript."""
        from kith.tools import run_tool

        tid = _task(db)
        run_tool("add_checklist_item", {"id": tid, "text": "from the archive"}, db)
        assert [i["text"] for i in _items(db, tid)] == ["from the archive"]

    def test_a_replayed_tick_still_works(self, db):
        from kith.tools import run_tool

        tid = _task(db)
        require("checklist").run(db, {"id": tid, "add": "a"})
        one = _items(db, tid)[0]["id"]
        run_tool("check_item", {"item_id": one, "done": True}, db)
        assert _items(db, tid)[0]["done"]

    def test_a_replayed_untick_still_works(self, db):
        """The case the argument mapping had to be designed around: `done` is a boolean on the
        old tool and has to stay a boolean on the new one, or this call cannot be translated."""
        from kith.tools import run_tool

        tid = _task(db)
        require("checklist").run(db, {"id": tid, "add": "a"})
        one = _items(db, tid)[0]["id"]
        require("checklist").run(db, {"items": one})
        run_tool("check_item", {"item_id": one, "done": False}, db)
        assert not _items(db, tid)[0]["done"]


class TestItIsSmaller:
    def test_the_merged_schema_costs_less_than_the_two_it_replaces(self):
        import json

        assert len(json.dumps(require("checklist").schema())) < 747
