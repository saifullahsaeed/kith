"""A builder is told how long it has before it starts, and nobody interrupts it near the end.

Until 2026-09-28 the last third of a builder's rounds opened with an injected "you're near the
end, make the edits now" — added because eleven builders on sadeef-v2 read until their budget ran
out and one wrote a patch. That was a patch over the cause: a builder that started knowing nothing
about the project and could not run what it wrote read until it was sure. It now starts with the
project's notes and can run the project's check and tests in its copy, and its brief states its
round count up front — information, not a mid-turn interruption. A builder that still comes back
empty is the script's to handle with `revise()`, where the person can see it happen.
"""

from __future__ import annotations

import re
from pathlib import Path

from kith.services import agent_loop
from kith.tools import workers


def _keeps_reading(seen: list[tuple[set[str], list[dict]]]):
    """A model that never stops reading: a different glob every round, so nothing looks repeated."""

    def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
        seen.append(({s["function"]["name"] for s in (tools or [])}, [dict(m) for m in convo]))
        call = {"function": {"name": "glob", "arguments": f'{{"pattern": "*{len(seen)}*"}}'}}
        yield {"type": "turn", "content": "", "tool_calls": [call], "stats": {}}

    return fake


def _run(db: Path, monkeypatch, kind: str, rounds: int = 6) -> list[tuple[set[str], list[dict]]]:
    seen: list[tuple[set[str], list[dict]]] = []
    monkeypatch.setattr(agent_loop, "_stream_once", _keeps_reading(seen))
    workers.run_agent(db, kind, "change the thing", "", rounds=rounds, context="")
    return seen


def _directives(convo: list[dict]) -> list[str]:
    return [str(m.get("content") or "") for m in convo if m.get("_directive")]


class TestTheBudgetIsSaidUpFront:
    def test_the_builder_brief_names_its_rounds(self, db: Path, monkeypatch):
        seen = _run(db, monkeypatch, "builder", rounds=7)
        system = str(seen[0][1][0]["content"])
        assert "You have 7 rounds" in system
        assert "{rounds}" not in system

    def test_every_tool_the_brief_names_is_one_a_builder_holds(self):
        named = set(re.findall(r"`([a-z_]+)`", workers.BUILDER_BRIEF))
        assert named, "the brief names no tool, so this test checks nothing"
        assert named <= workers.ROLES["builder"]["given"]()

    def test_every_tool_the_verifier_brief_names_is_one_it_holds(self):
        named = set(re.findall(r"`([a-z_]+)`", workers.VERIFIER_BRIEF))
        assert named <= workers.ROLES["verifier"]["given"]()


class TestNobodyIsInterruptedNearTheEnd:
    def test_no_role_is_handed_a_landing_directive(self, db: Path, monkeypatch):
        for kind in workers.ROLES:
            seen = _run(db, monkeypatch, kind)
            assert len(seen) >= 6
            told = [said for _, convo in seen for said in _directives(convo)]
            assert agent_loop._LANDING_DIRECTIVE not in told, f"a {kind} was told the turn's landing"
            assert not any("near the end" in said for said in told), f"a {kind} was told the end is near"

    def test_a_builder_keeps_its_edit_and_check_tools_to_the_last_round(self, db: Path, monkeypatch):
        seen = _run(db, monkeypatch, "builder")
        last_offered = seen[-1][0]
        assert {"edit_files", "write_file", "read_file", "check_code", "run_tests"} <= last_offered
