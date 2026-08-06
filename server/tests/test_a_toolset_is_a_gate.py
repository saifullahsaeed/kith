"""What he is offered is what he may run.

Three separate mechanisms in this codebase narrow the toolset mid-turn, and until now not
one of them was enforced:

* the per-mode sets in `autonomy/toolsets.py`;
* the landing reserve, which takes work tools away for the last rounds so a turn cannot
  spend all forty gathering and finish having produced nothing;
* the narrower set after a delegation, which leaves planning but removes doing.

All three were passed only to `tool_schemas(only=...)`, which decides what the model is
*shown*. `run_tool` took the name, resolved it against the whole registry, and ran it.

Measured before the fix: in `breakout` mode, `remember` and `add_task` both succeeded. So the
landing reserve was a suggestion — a model that named `web_search` anyway got one, and the
one guard against a turn gathering forever could be stepped over by ignoring it.

It matters more now than it did, because a subagent's isolation is exactly this list. A child
scoped to reading is only really scoped to reading if the scope is a gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import kith.tools
from kith.autonomy.toolsets import _ALLOW
from kith.services import agent_loop
from kith.tools import run_tool, tool_schemas


class TestRunToolRefusesWhatWasNotOffered:
    def test_a_tool_outside_the_set_is_refused(self, db: Path):
        out = run_tool("remember", {"content": "x"}, db, allow={"journal", "recall"})
        assert out["ok"] is False
        assert "remember" in out["error"]

    def test_the_refusal_names_the_tool_so_he_can_act_on_it(self, db: Path):
        """ "Something went wrong" is not actionable. "`x` is not available here" is."""
        error = run_tool("shell", {"command": "ls"}, db, allow={"journal"})["error"]
        assert "shell" in error
        assert "not available" in error

    def test_it_does_not_list_the_whole_set_back_at_him(self, db: Path):
        """He already has the schemas. On a 55-tool set, spelling them out is most of a
        round's budget spent repeating what he was just sent.

        Against an explicit narrow set, not a mode's: this asserts something about the shape of
        a refusal, and borrowing a mode to get one couples it to that mode's contents. It used
        to read `_ALLOW["breakout"]`, which was the six-tool loadout — and broke the day
        breakout was widened to the work set for reasons that had nothing to do with error
        messages.
        """
        out = run_tool("shell", {"command": "ls"}, db, allow={"journal", "recall", "list_tasks"})
        assert out["ok"] is False
        assert len(out["error"]) < 200

    def test_a_tool_inside_the_set_runs_normally(self, db: Path):
        out = run_tool("journal", {"entry": "allowed"}, db, allow={"journal"})
        assert out["ok"] is True

    def test_no_allow_list_means_no_restriction(self, db: Path):
        """The ordinary case, and every caller that does not scope its tools."""
        assert run_tool("journal", {"entry": "unscoped"}, db)["ok"] is True
        assert run_tool("journal", {"entry": "explicitly none"}, db, allow=None)["ok"] is True

    def test_an_empty_set_permits_nothing(self, db: Path):
        """Distinct from None, and it has to be — `set()` is what a filter that matched
        nothing produces, and treating it as "no restriction" would open the gate widest at
        exactly the moment it was meant to be shut."""
        assert run_tool("journal", {"entry": "x"}, db, allow=set())["ok"] is False

    def test_a_refused_call_leaves_nothing_behind(self, db: Path):
        from kith.infra.db import repositories as repo

        run_tool("journal", {"entry": "must not be written"}, db, allow={"recall"})
        assert repo.journal.list_journal(db, 10) == []

    def test_a_name_that_is_not_a_tool_at_all_is_still_handled(self, db: Path):
        """Refusing must not shadow the existing unknown-tool answer, which is what tells him
        he invented something."""
        out = run_tool("teleport", {}, db, allow={"journal"})
        assert out["ok"] is False


class TestTheModesThemselves:
    @pytest.mark.parametrize("mode", sorted(_ALLOW))
    def test_a_mode_refuses_a_tool_it_does_not_offer(self, db: Path, mode: str):
        offered = {s["function"]["name"] for s in tool_schemas(db, only=_ALLOW[mode])}
        outside = sorted(set(kith.tools.registry.all_tools()) - offered)
        if not outside:
            pytest.skip(f"{mode} offers everything, so there is nothing to refuse")
        out = run_tool(outside[0], {}, db, allow=offered)
        assert out["ok"] is False, f"{mode} ran {outside[0]!r}, which it does not offer"

    def test_the_measured_escapes_are_still_shut(self, db: Path):
        """The two calls that actually ran before the gate existed, against the loadout they
        ran under — `breakout`'s original six tools.

        Kept as a literal set rather than read from `_ALLOW`. This is a regression test for the
        gate, and the thing it regresses against is a measurement taken at a moment in time; if
        a mode's contents change, that does not make the measurement untrue. Breakout has since
        been widened to the work set, precisely so that `remember` and `add_task` ARE reachable
        while breaking a loop — the six-tool version left him able to describe a different
        approach and not to take one.
        """
        six = {"read_skill", "list_tasks", "update_task", "journal", "update_project", "recall"}
        offered = {s["function"]["name"] for s in tool_schemas(db, only=six)}
        for name, args in (("remember", {"content": "x"}), ("add_task", {"goal": "x"})):
            assert name not in offered
            assert run_tool(name, args, db, allow=offered)["ok"] is False


class TestTheLoopEnforcesWhatItOffered:
    def test_the_gate_is_derived_from_the_finished_schema_list(self):
        """Not from `allow`. The landing and delegated filters run *after* it, so reading
        `allow` would enforce the widest of the three and silently miss the other two — which
        are the ones that exist to stop a turn running away."""
        source = agent_loop.__loader__.get_source("kith.services.agent_loop")
        assert 'permitted = {s["function"]["name"] for s in schemas}' in source
        # And it is passed to the runner, on both the single and the parallel path.
        assert source.count("_run(batch[0], agent_db_path, permitted)") == 1
        assert "lambda step, allow=permitted" in source

    def test_run_passes_it_through(self, db: Path):
        step = {"name": "journal", "arguments": {"entry": "x"}, "repeat": False}
        assert agent_loop._run(step, db, {"recall"})["ok"] is False
        assert agent_loop._run(step, db, {"journal"})["ok"] is True

    def test_a_repeated_call_is_still_short_circuited_first(self, db: Path):
        """The loop-detection note must not be replaced by a refusal — they answer different
        questions and he needs the one about repeating."""
        step = {"name": "journal", "arguments": {"entry": "x"}, "repeat": True}
        assert "already made this exact call" in str(agent_loop._run(step, db, set()))


class TestPollingToolsAreExemptFromTheThrashGuard:
    """`run_tests`'s own tool description says to call it again with the same arguments to
    check on a still-running suite — as does `check_process` for anything else long-running.
    For every other tool, the same pattern (identical call, nothing to show for it) is a
    stall. The guard has to tell these apart, or a ten-minute suite can only ever be checked
    on twice before the model is told to stop and answer without knowing the result."""

    def test_an_ordinary_tool_is_flagged_on_the_third_identical_call(self):
        assert agent_loop._is_repeat("journal", seen=0) is False
        assert agent_loop._is_repeat("journal", seen=1) is False
        assert agent_loop._is_repeat("journal", seen=2) is True

    def test_run_tests_is_never_flagged_however_many_times_its_seen(self):
        assert agent_loop._is_repeat("run_tests", seen=2) is False
        assert agent_loop._is_repeat("run_tests", seen=30) is False

    def test_check_process_is_never_flagged_either(self):
        assert agent_loop._is_repeat("check_process", seen=10) is False
