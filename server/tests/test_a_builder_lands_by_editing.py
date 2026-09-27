"""A builder's last rounds are for making the change, not for reading more.

Builders used to run with no landing at all — `landing_reserve=0`, on the reasoning that an
agent's output is its final message. True of a scout. A builder's output is the edit it makes in
its copy, and with nothing telling it to stop looking it looked until the budget ran out. Measured
on sadeef-v2, 2026-09-26: eleven builders across two workflows, 17 to 41 tool calls each, one patch
between them, and the reports said so — "I ran out of my tool budget while reading, before I wrote
a line."

So a builder lands like a turn does, with its own words: the last third of its rounds opens with
"make the edits now", and the tools to make them are still there when it does.
"""

from __future__ import annotations

import re
from pathlib import Path

from kith.services import agent_loop
from kith.tools import workers


def _keeps_reading(seen: list[tuple[set[str], str]]):
    """A model that never stops reading: a different glob every round, so nothing looks repeated."""

    def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
        seen.append(({s["function"]["name"] for s in (tools or [])}, str(convo[-1].get("content") or "")))
        call = {"function": {"name": "glob", "arguments": f'{{"pattern": "*{len(seen)}*"}}'}}
        yield {"type": "turn", "content": "", "tool_calls": [call], "stats": {}}

    return fake


def _run(db: Path, monkeypatch, kind: str) -> list[tuple[set[str], str]]:
    seen: list[tuple[set[str], str]] = []
    monkeypatch.setattr(agent_loop, "_stream_once", _keeps_reading(seen))
    workers.run_agent(db, kind, "change the thing", "", rounds=6)
    return seen


class TestABuilderIsToldToWrite:
    def test_its_last_third_opens_with_the_builder_landing(self, db: Path, monkeypatch):
        seen = _run(db, monkeypatch, "builder")
        told = [i for i, (_, said) in enumerate(seen) if said == workers.BUILDER_LANDING]
        assert told == [4], "six rounds, the last two for writing — the directive opens round five"

    def test_it_can_still_edit_when_it_is_told_to(self, db: Path, monkeypatch):
        """The failure this whole reserve has had before: an instruction and a toolset disagreeing
        on the same request. See `test_landing_can_finish_the_work.py`."""
        seen = _run(db, monkeypatch, "builder")
        offered = next(names for names, said in seen if said == workers.BUILDER_LANDING)
        assert {"edit_files", "write_file", "read_file"} <= offered

    def test_it_is_not_told_the_turn_s_landing(self, db: Path, monkeypatch):
        """Which says to file a deliverable and tick a checklist — neither of which a builder has."""
        seen = _run(db, monkeypatch, "builder")
        assert not any(said == agent_loop._LANDING_DIRECTIVE for _, said in seen)

    def test_every_tool_the_landing_names_is_one_a_builder_holds(self):
        named = set(re.findall(r"`([a-z_]+)`", workers.BUILDER_LANDING))
        assert named, "the landing names no tool, so this test checks nothing"
        assert named <= workers.ROLES["builder"]["given"]()


class TestAScoutStillHasNoLanding:
    def test_a_scout_that_keeps_reading_is_never_told_to_land(self, db: Path, monkeypatch):
        """Its report is its output and `_final_answer` already forces one."""
        seen = _run(db, monkeypatch, "scout")
        assert len(seen) >= 6
        assert not any(said in (workers.BUILDER_LANDING, agent_loop._LANDING_DIRECTIVE) for _, said in seen)
