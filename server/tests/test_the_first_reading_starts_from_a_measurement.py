"""A turn's first meter reading is costed at what the last turn on that model measured.

A `ContextBudget` is born per turn and its first wire measurement arrives only after the first
request, so the reading the person saw when a turn started was always costed at the seed: 3.7
characters per token in all 353 turns recorded Sep 13-27, and in every single-round turn the only
reading there was. Against what those first requests were actually billed, that was 3.4% out at
the median and 43.6% at p90. Starting from the model's last first-round ratio: 0.7% and 19.3%.

What these hold is the edge of it: carried per model, only ever a starting point, and never
mistaken for a measurement — `is_tight` still waits for this turn's own.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith.domain.chat import Config
from kith.llm import budget, ledger
from kith.llm.budget import SEED_CHARS_PER_TOKEN, ContextBudget
from kith.services.turn import frozen


@pytest.fixture(autouse=True)
def nothing_measured_yet(monkeypatch):
    """Each test starts as a freshly started server does, with no model measured."""
    monkeypatch.setattr(budget, "_FIRST_ROUND", {})


class TestTheRatioCarriesToTheNextTurn:
    def test_the_next_turn_on_a_model_starts_where_the_last_one_measured(self):
        ContextBudget(window=1_000_000, model="deepseek/v4").observe(10_000, 42_000)
        assert ContextBudget(window=1_000_000, model="deepseek/v4").chars_per_token == pytest.approx(4.2)

    def test_the_first_reading_is_costed_at_it(self):
        """The number the person sees at turn start — the whole point of the change."""
        ContextBudget(model="deepseek/v4").observe(10_000, 42_000)
        room = ContextBudget(model="deepseek/v4")
        convo = [{"role": "user", "content": "x" * 42_000}]
        reading = ledger.take(convo, chars_per_token=room.chars_per_token)
        assert reading.used == pytest.approx(budget.conversation_chars(convo) / 4.2, abs=2)

    def test_it_is_the_first_round_that_is_carried_not_the_last(self):
        """A turn's fortieth request is mostly tool output and tokenizes differently from its
        first; the next turn's first request looks like this turn's first."""
        room = ContextBudget(model="deepseek/v4")
        room.observe(10_000, 42_000)
        room.observe(100_000, 300_000)
        assert room.chars_per_token == pytest.approx(3.0), "the turn itself follows its own rounds"
        assert ContextBudget(model="deepseek/v4").chars_per_token == pytest.approx(4.2)

    def test_the_carried_ratio_is_held_to_the_same_band(self):
        ContextBudget(model="odd").observe(10_000, 5_000)
        assert ContextBudget(model="odd").chars_per_token == budget.MIN_RATIO


class TestItIsOnlyEverAStartingPoint:
    def test_another_model_starts_from_the_seed(self):
        """The tokenizer is the model's. Another model's ratio is not a better guess than 3.7."""
        ContextBudget(model="deepseek/v4").observe(10_000, 42_000)
        assert ContextBudget(model="openai/gpt").chars_per_token == SEED_CHARS_PER_TOKEN

    def test_no_model_remembers_nothing(self):
        ContextBudget().observe(10_000, 42_000)
        assert budget._FIRST_ROUND == {}
        assert ContextBudget().chars_per_token == SEED_CHARS_PER_TOKEN

    def test_a_ratio_passed_in_is_the_ratio_used(self):
        ContextBudget(model="deepseek/v4").observe(10_000, 42_000)
        assert ContextBudget(model="deepseek/v4", chars_per_token=3.1).chars_per_token == 3.1

    def test_the_turns_own_first_measurement_replaces_it(self):
        ContextBudget(model="deepseek/v4").observe(10_000, 42_000)
        room = ContextBudget(model="deepseek/v4")
        room.observe(10_000, 35_000)
        assert room.chars_per_token == pytest.approx(3.5)

    def test_a_carried_ratio_is_not_a_measurement(self):
        """`is_tight` never fires before the turn has an anchor of its own, and a better starting
        ratio is not an anchor — it says nothing about *this* prompt's size."""
        ContextBudget(model="deepseek/v4").observe(10_000, 42_000)
        room = ContextBudget(window=10_000, reserve=0, model="deepseek/v4")
        assert not room.is_tight(10_000_000)
        assert room.projected(42_000) == 10_000


class TestTheTurnIsBuiltWithIt:
    def test_a_turn_starts_from_its_models_last_measurement(self, db: Path):
        """The wiring: `frozen.begin` builds the turn's budget, so it has to name the model."""
        ContextBudget(model="deepseek/v4").observe(10_000, 42_000)
        config = Config(model="deepseek/v4", num_ctx=0, num_predict=1_000, system="", think=False)
        assert frozen.begin(config, db, "", None).room.chars_per_token == pytest.approx(4.2)
