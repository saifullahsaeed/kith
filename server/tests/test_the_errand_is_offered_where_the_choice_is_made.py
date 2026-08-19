"""The three tools he reaches for instead of an errand say when an errand is the better move.

Measured 2026-08-19 over every transcript on this machine: **25 `delegate_subtask` calls, all
25 immediately after being told to.** "sub agent test spawn one", "do like a couple of them",
"again", "try agents". Zero spontaneous, ever — against 2,523 `read_file`, 1,502 `grep` and
642 `outline`. When told, he uses it well: three errands came back with exact `file:line`
citations. So this was never capability, and it was never the tool being hidden.

Two causes, and this file pins the fix for both.

**The pitch argued the wrong thing.** It sold rounds — "a dozen reads to learn one sentence".
Then `read_file` and `grep` became plural on the same day and a dozen reads became one call, so
the tool was arguing against a world that no longer existed. What survives is context: twelve
files read here sit in the window for the rest of the turn and are re-sent every round; twelve
files an errand reads cost its summary. That is the argument now, and it is the stronger one.

**An order beats an offer.** `outline` says "Do this BEFORE read_file on anything you do not
already know" — an imperative firing at exactly the moment an errand would apply — while
`delegate_subtask` said "use it whenever". So the pointer moved to where the choice is actually
made, which is the pattern this codebase already uses for `grep` → `find_symbol`: the schemas
are re-sent on every round, and a note there is read at the moment of the decision rather than
80k tokens earlier at the top of a cached persona.

Asserted rather than left to prose, because the failure mode is silent: someone rewords one of
these four descriptions, the pointer goes, and the only symptom is a number in a transcript
nobody is looking at.
"""

from __future__ import annotations

import pytest

from kith.tools import registry


def described(name: str) -> str:
    entry = registry.get(name)
    assert entry is not None, f"{name} is not registered"
    return entry.description


class TestTheLookingToolsPointAtTheErrand:
    @pytest.mark.parametrize("name", ["read_file", "grep", "outline"])
    def test_each_one_names_it(self, name: str):
        """At the point of use, in the block that is re-sent every round — not in the persona,
        which sits at the top of a cached prefix and is 300k tokens behind the decision by the
        time it matters."""
        assert "delegate_subtask" in described(name), (
            f"{name} no longer tells him an errand is an option. That pointer is the only thing "
            "reaching him at the moment he chooses, and without it the measured behaviour is "
            "zero spontaneous delegations in 4,667 looking calls."
        )

    @pytest.mark.parametrize("name", ["read_file", "grep", "outline"])
    def test_each_one_says_when(self, name: str):
        """A pointer with no trigger is an offer, and offers lose to `outline`'s imperative.
        Each names the situation: files you have not read, a codebase you do not know, a
        subsystem you are getting your bearings in."""
        text = described(name).lower()
        assert "errand" in text


class TestThePitchArguesContextRatherThanRounds:
    def test_it_does_not_sell_rounds_any_more(self):
        """`read_file`/`outline` take a list of paths and `grep` a list of patterns, so "a dozen
        reads costs a dozen rounds" is simply false now. An argument the model can check and
        find false is worse than no argument."""
        text = described("delegate_subtask")

        assert "CONTEXT, NOT ROUNDS" in text

    def test_it_says_what_an_errand_actually_costs_you(self):
        text = described("delegate_subtask").lower()

        assert "window" in text and "re-sent" in text

    def test_it_states_a_trigger_a_model_can_evaluate(self):
        """Compare `outline`'s "a 2,000-line module costs you the whole module" — concrete, and
        obeyed. "Whenever it would cost you a lot of looking" is a judgement with no
        measurement behind it, and it was not obeyed once."""
        text = described("delegate_subtask")

        assert "REACH FOR ONE WHEN" in text
        assert "three or four files" in text

    def test_it_still_says_to_send_them_in_one_round(self):
        """The measured failure the day this shipped: three errands sent in three separate
        rounds ran serially, so the fan-out that `_PARALLEL_SAFE` exists for did not happen.
        15 of 18 delegating rounds sent exactly one."""
        text = described("delegate_subtask")

        assert "ONE round" in text
        assert "parallel" in text
