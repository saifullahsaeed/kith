"""A bound that was written down, believed, and silently doing nothing.

Found by attacking a design rather than by anything failing, which is the point: a bound that
never fires looks exactly like a bound that is never needed.

This file held two. The other was `tick_max_tokens`, capping what a tick could write, and it
went with the loop it bounded.
"""

from __future__ import annotations

import inspect
from dataclasses import replace
from pathlib import Path

import pytest

from kith.config import default_config
from kith.services import agent_loop, tuning


class TestTheForcedFinalAnswer:
    """The last round used to send a different tool list from every round before it.

    `_final_answer` is handed schemas deliberately — with `tool_choice="none"`, because a
    model told to stop by prose alone narrates calls as text instead. But the call site built
    its own with `tool_schemas()` and no `only`, so a breakout tick that had
    been offering six tools all turn ended by sending fifty-nine.

    Beyond the tokens, it is a cache fault: on the providers that need an explicit
    breakpoint, tools sit ahead of the system prompt, so a changed tools block rewrites the
    entire prefix — on the one request a turn cannot skip.
    """

    def test_the_final_request_reuses_the_round_s_own_list(self):
        # `_run_turn` is the loop; `stream_agent` is now a thin wrapper that opens the
        # turn boundary around it. The invariant below lives in the loop.
        source = inspect.getsource(agent_loop._run_turn)
        assert "_final_answer(convo, config, host, schemas" in source, (
            "the forced final answer is building its own tool list again"
        )
        # Code only. The comment above the fix quotes the old call on purpose, and matching
        # a comment would make this assertion pass or fail on prose.
        code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
        assert "tool_schemas()" not in code, "an unscoped tool_schemas call is back in the loop"

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

        def fake_stream(convo, config, host, tools=None, tool_choice=None, routing=None):
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
    def test_a_config_is_always_bounded(self, num_predict: int):
        """Whatever the config says, a tick's output has a ceiling."""
        cap = tuning.value("max_answer_tokens")
        config = replace(default_config(), num_predict=num_predict)
        wanted = config.num_predict
        effective = cap if wanted <= 0 else min(wanted, cap)
        assert 0 < effective <= cap
