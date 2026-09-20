"""How much tool output stays whole is a share of the window, not a constant.

`test_a_stable_history_stays_cacheable` gated compaction in September for a good, measured
reason: `live_tool_chars` is 80,000, which against a 1,050,000-token window is 1.9% of the
room, and trimming to it made Kith re-read `ModelsSettings.tsx` fifteen times — 54% of every
read he made was of a file he had already read. Gating it fixed that.

What the gate left behind is a system with only two states, and no middle:

* under 80% of the window — **nothing is trimmed at all**, ever;
* over it — the live set is slammed to 80,000 characters, and the re-reading returns.

Measured on 478 real turns, the first state is the only one that ever happens. The median turn
sends 3.09M prompt tokens across 16 rounds and the p90 sends 33.5M across 83; the fold fires at
838,860 tokens of *live* context, which those turns never reach in a single round. So by round
sixty he is reading sixty rounds of shell output, all of it equally present, and the machinery
built to prevent exactly that has never run.

Neither state is wrong about its own case. The constant is what is wrong: 80,000 characters was
set for a small model and never moved when the window became a million tokens. Sized as a share
instead, the same mechanism leaves a normal turn completely alone — which is what September was
protecting — and engages on the turns that actually got away.
"""

from __future__ import annotations

import json
from typing import Any

from kith.services.turn import history

#: A million-token model, as configured on this install.
BIG = 1_050_000
#: What a default local install reports.
SMALL = 40_960


def _convo(results: int, size: int = 9_000) -> list[dict[str, Any]]:
    convo: list[dict[str, Any]] = [
        {"role": "system", "content": "persona"},
        {"role": "user", "content": "go"},
    ]
    for i in range(results):
        convo.append(
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": f"c{i}",
                        "function": {"name": "read_file", "arguments": json.dumps({"path": f"/f{i}.tsx"})},
                    }
                ],
            }
        )
        convo.append(
            {"role": "tool", "tool_call_id": f"c{i}", "name": "read_file", "content": f"F{i}:" + "x" * size}
        )
    return convo


def _held(convo: list[dict[str, Any]]) -> int:
    return sum(len(str(m.get("content") or "")) for m in convo)


class TestTheBudgetFollowsTheWindow:
    def test_a_big_window_buys_a_big_live_set(self):
        assert history.live_tool_budget(BIG) > 400_000

    def test_a_small_window_is_not_given_more_than_it_has(self):
        """The share has to shrink with the window or a 40k model is handed a budget several
        times its own context, which is the 80,000-character bug in the other direction."""
        assert history.live_tool_budget(SMALL) < 80_000

    def test_an_unknown_window_falls_back_to_the_setting(self):
        """0 is load-bearing everywhere else in this loop and stays so here. Nobody can say how
        much room there is, so the honest answer is the absolute budget this always used."""
        assert history.live_tool_budget(0) == 80_000


class TestWhatActuallyGetsTrimmed:
    def test_a_normal_turn_on_a_big_window_is_left_completely_alone(self):
        """The September property, and it still holds. Twelve nine-thousand-character reads is
        108,000 characters — a real research turn, and nowhere near the room a million tokens
        buys. Touching it is what caused the re-reading."""
        convo = _convo(12)
        before = _held(convo)
        history._compact_tool_history(convo, window=BIG)
        assert _held(convo) == before

    def test_a_turn_that_ran_away_is_finally_trimmed(self):
        """A hundred reads is 900,000 characters in one round. This is the turn the complaint
        is about, and until now it was left whole by design."""
        convo = _convo(100)
        before = _held(convo)
        history._compact_tool_history(convo, window=BIG)
        assert _held(convo) < before

    def test_the_newest_results_are_the_ones_kept(self):
        """Age-out, not a blanket cut. What he just looked at has to survive, or the round after
        the trim re-reads the thing the trim threw away."""
        convo = _convo(100)
        history._compact_tool_history(convo, window=BIG)
        results = [m for m in convo if m.get("role") == "tool"]
        assert len(results[-1]["content"]) > 9_000  # newest, whole
        assert len(results[0]["content"]) < 9_000  # oldest, stubbed


class TestThePrefixStillSurvivesWhereItMatters:
    def test_a_turn_under_the_budget_is_never_rewritten(self):
        """Cache safety, which is the whole reason the gate went in. Below the budget, round
        N+1's history must still contain round N's unchanged as a prefix."""
        at_n, at_n_plus_1 = _convo(12), _convo(13)
        history._compact_tool_history(at_n, window=BIG)
        history._compact_tool_history(at_n_plus_1, window=BIG)
        assert at_n_plus_1[: len(at_n)] == at_n

    def test_it_holds_across_every_round_below_the_budget(self):
        previous: list[dict] | None = None
        for results in range(1, 50):  # 50 x 9,000 = 450,000, still inside the budget
            current = _convo(results)
            history._compact_tool_history(current, window=BIG)
            if previous is not None:
                assert current[: len(previous)] == previous, f"prefix broke at {results} results"
            previous = current
