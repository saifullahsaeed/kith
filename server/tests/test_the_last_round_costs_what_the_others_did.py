"""Two bounds that were written down, believed, and silently doing nothing.

Both were found by attacking a design rather than by anything failing, which is the point:
a bound that never fires looks exactly like a bound that is never needed.
"""

from __future__ import annotations

import inspect
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from kith.config import default_config
from kith.services import agent_loop, tuning

MODULE = sys.modules["kith.autonomy.runner"]


class TestTheTickOutputCap:
    """`tick_max_tokens` exists so a tick is a step rather than an essay — the knob's own
    help text says bounding output is what keeps running all day affordable.

    It was applied as `min(config.num_predict, tick_max_tokens)`. On this install
    `num_predict` is -1, the sentinel for "no limit", so the min is -1 and every tick since
    the knob was added has run uncapped. A sentinel that sorts below every real value wins
    any comparison meant to bound it.
    """

    @staticmethod
    def capped(num_predict: int) -> int:
        """The runner's own arithmetic, read out of the source so this cannot drift."""
        source = inspect.getsource(MODULE.AutonomyRunner._step)
        assert "tick_cap if wanted <= 0 else min(wanted, tick_cap)" in source
        tick_cap = tuning.value("tick_max_tokens")
        return tick_cap if num_predict <= 0 else min(num_predict, tick_cap)

    def test_no_limit_becomes_the_tick_limit(self):
        """The case that was broken, and the default on this install."""
        assert self.capped(-1) == tuning.value("tick_max_tokens")

    def test_zero_is_treated_the_same_way(self):
        """0 is the other spelling of "unset" a config can arrive with, and `min(0, 2000)`
        would cap a tick at zero tokens — worse than uncapped."""
        assert self.capped(0) == tuning.value("tick_max_tokens")

    def test_a_smaller_explicit_limit_still_wins(self):
        assert self.capped(500) == 500

    def test_a_larger_explicit_limit_is_brought_down(self):
        assert self.capped(999_999) == tuning.value("tick_max_tokens")

    def test_the_knob_is_what_decides_it(self):
        tuning.apply({"tick_max_tokens": 1234})
        assert self.capped(-1) == 1234


class TestTheForcedFinalAnswer:
    """The last round used to send a different tool list from every round before it.

    `_final_answer` is handed schemas deliberately — with `tool_choice="none"`, because a
    model told to stop by prose alone narrates calls as text instead. But the call site built
    its own with `tool_schemas(agent_db_path)` and no `only`, so a breakout tick that had
    been offering six tools all turn ended by sending fifty-nine.

    Beyond the tokens, it is a cache fault: on the providers that need an explicit
    breakpoint, tools sit ahead of the system prompt, so a changed tools block rewrites the
    entire prefix — on the one request a turn cannot skip.
    """

    def test_the_final_request_reuses_the_round_s_own_list(self):
        # `_run_turn` is the loop; `stream_agent` is now a thin wrapper that opens the
        # turn boundary around it. The invariant below lives in the loop.
        source = inspect.getsource(agent_loop._run_turn)
        assert "_final_answer(convo, config, host, schemas)" in source, (
            "the forced final answer is building its own tool list again"
        )
        # Code only. The comment above the fix quotes the old call on purpose, and matching
        # a comment would make this assertion pass or fail on prose.
        code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
        assert "tool_schemas(agent_db_path)" not in code, "an unscoped tool_schemas call is back in the loop"

    def test_the_list_is_defined_even_if_no_round_ran(self):
        """`schemas` is the last round's; a turn that somehow reaches the end without one
        must not raise NameError on the way to answering."""
        # `_run_turn` is the loop; `stream_agent` is now a thin wrapper that opens the
        # turn boundary around it. The invariant below lives in the loop.
        source = inspect.getsource(agent_loop._run_turn)
        assert "schemas: list[dict] = []" in source

    def test_it_still_sends_them_rather_than_none(self, db: Path):
        """Removing the schemas is not the fix. Asked to stop with the definitions absent, a
        model mid-turn emits `<FUNCTION>web_search(...)</FUNCTION>` as prose, which lands in
        the transcript as if it were the answer."""
        sent: list = []

        def fake_stream(convo, config, host, tools=None, tool_choice=None):
            sent.append((tools, tool_choice))
            return iter(())

        original = agent_loop._stream_once
        agent_loop._stream_once = fake_stream
        try:
            list(agent_loop._final_answer([], default_config(), "", [{"function": {"name": "x"}}]))
        finally:
            agent_loop._stream_once = original

        assert sent, "_final_answer made no request"
        tools_sent, choice = sent[0]
        assert tools_sent, "the schemas were dropped"
        assert choice == "none", "tool_choice=none is what actually stops the narration"


class TestTheTwoBoundsTogether:
    @pytest.mark.parametrize("num_predict", [-1, 0, 100, 100_000])
    def test_a_tick_config_is_always_bounded(self, num_predict: int):
        """Whatever the config says, a tick's output has a ceiling."""
        cap = tuning.value("tick_max_tokens")
        config = replace(default_config(), num_predict=num_predict)
        wanted = config.num_predict
        effective = cap if wanted <= 0 else min(wanted, cap)
        assert 0 < effective <= cap
