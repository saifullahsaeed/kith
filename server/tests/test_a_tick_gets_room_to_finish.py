"""A tick was given a third of chat's rounds, so work bigger than that was cut into pieces.

`max_rounds=16` was a literal in the runner while chat read the `max_rounds` setting (40). After
the landing reserve that left **12 working rounds against chat's 36** — and a task that needed
more was not extended, it was chopped, with every piece starting from an empty context and
re-reading the codebase to work out where it was.

Measured on a real board: a median task took 7 ticks, one took 20. Eight consecutive ticks on a
single piece of work spent roughly 2M prompt tokens and ended with the loop-breaker stepping in to
stop him re-verifying work that was already done. The per-tick numbers barely moved when the
handoff was improved — 245k prompt before, 243k after — because the cost was never the handoff, it
was starting again.

The output cap was the other half of the same gag: 2,000 tokens *per response*, so any round that
needed to write a file was truncated. Fifty rounds at 2,000 tokens each would have been just as
unable to finish, in more rounds.
"""

from __future__ import annotations

from kith.services import tuning


def _runner_source() -> str:
    """The runner module's source.

    Via `sys.modules`, because `from kith.autonomy import runner` hands back the
    **AutonomyRunner instance** — the package exports a singleton under the same name as the
    module, so the import shadows it and `runner.__loader__` is an attribute error on an object
    that has no idea what you wanted. Documented here because it has caught me four separate
    times in one session and reads like a typo rather than a design.
    """
    import sys

    return sys.modules["kith.autonomy.runner"].__loader__.get_source("kith.autonomy.runner")


class TestTheRoundBudget:
    def test_it_comes_from_a_setting_not_a_literal(self):
        """Hardcoded, it could not be raised without a code change and a rebuild — which for a
        packaged desktop app means it could not be raised at all."""
        source = _runner_source()
        assert 'max_rounds=int(tuning.value("tick_max_rounds"))' in source
        assert "max_rounds=16" not in source

    def test_a_tick_is_no_longer_starved_next_to_chat(self):
        assert tuning.value("tick_max_rounds") >= tuning.value("max_rounds")

    def test_the_working_budget_is_what_actually_grew(self):
        """Rounds minus the landing reserve is the number that decides whether a task finishes.
        The reserve scales with the budget, so raising rounds cannot accidentally starve landing."""
        rounds = int(tuning.value("tick_max_rounds"))
        reserve = min(tuning.value("landing_reserve"), max(2, rounds // 3))
        assert rounds - reserve >= 40, "a tick still cannot outwork a chat turn"
        assert reserve >= 2, "landing must always keep something back"

    def test_it_is_read_per_step_so_a_change_lands_on_the_next_tick(self):
        """Captured once at construction it would need a restart, and the whole point of these
        being settings is that you can turn one up while he is working."""
        source = _runner_source()
        assert "tuning.value(\"tick_max_rounds\")" in source


class TestTheOutputCap:
    def test_a_step_can_write_a_real_file(self):
        """2,000 tokens is roughly 60 lines. It is per *response*, so a single round that had to
        write a module or a multi-hunk edit came back truncated."""
        assert tuning.value("tick_max_tokens") >= 8_000

    def test_but_it_is_still_bounded(self):
        """Not uncapped. An unattended step with no ceiling on output is how one runs for an hour
        writing prose nobody asked for; the ceiling just has to clear a real file."""
        knob = tuning.value("tick_max_tokens")
        assert 0 < knob <= 32_000


class TestTheBackstopsStillMakeSenseAtThisSize:
    def test_the_per_task_tick_cap_is_now_generous_rather_than_tight(self):
        """With 46 working rounds a task should finish in one or two ticks, so 12 stops being a
        limit on ordinary work and becomes what it was meant to be — a runaway backstop."""
        assert tuning.value("task_tick_cap") >= 8

    def test_a_long_tick_can_still_fold_instead_of_dying(self):
        """A 46-round tick builds a far bigger prompt than a 12-round one — the largest measured
        was 601k tokens at 28 calls. The fold has to be reachable below the window, or a long tick
        ends in a 400 instead of a summary."""
        from kith.services import agent_loop

        window = 1_050_000
        fold_at = window * agent_loop._CHARS_PER_TOKEN * agent_loop._FOLD_ABOVE_SHARE
        assert fold_at / agent_loop._CHARS_PER_TOKEN < window, "fold must fire before the window"
        assert agent_loop._MAX_FOLDS >= 1
