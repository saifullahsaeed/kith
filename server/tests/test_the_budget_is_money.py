"""A session's budget is what it cost, not how many tokens went past.

A real working session was stopped with:

    I stopped this session — it ran through 6,544,155 tokens, past its budget of 5,000,000.

It had spent about twenty-six cents. The meter charged `tokens_in`, the whole prompt with
cached re-reads included, on the reasoning that it was the number a person reacts to. It is
— and that is why it was the wrong one to enforce: at a 78% cache hit it runs more than four
times faster than the work being done, against a ceiling written in the same units.

The provider reports cost on every call and it was already being summed for the dashboard.
It simply was not the thing being enforced.
"""

from __future__ import annotations

import sys

import pytest


def runner_module():
    return sys.modules["kith.autonomy.runner"]


@pytest.fixture
def runner(db, monkeypatch):
    one = runner_module().AutonomyRunner()
    monkeypatch.setattr(runner_module(), "AGENT_DB_PATH", db)
    return one


def stopped(runner, conversation_id: str) -> bool:
    return conversation_id in runner._session_capped


class TestChargingMoney:
    def test_a_cheap_session_is_not_stopped_however_many_tokens(self, runner):
        """The exact case that went wrong: millions of tokens, pennies of cost."""
        runner._charge_session("c1", uncached_in=6_544_155, cost_usd=0.26)
        assert not stopped(runner, "c1")

    def test_an_expensive_one_is_stopped_on_few_tokens(self, runner):
        """And the inverse, which the old meter would have let run all night."""
        runner._charge_session("c1", uncached_in=40_000, cost_usd=9.50)
        assert stopped(runner, "c1")

    def test_cost_accumulates_across_ticks(self, runner):
        for _ in range(5):
            runner._charge_session("c1", uncached_in=10_000, cost_usd=0.70)
        assert stopped(runner, "c1"), "$3.50 across five ticks should reach a $3 cap"

    def test_the_message_is_in_money(self, db, runner):
        from kith.infra.db import repositories as repo

        runner._charge_session("c1", uncached_in=1_000, cost_usd=9.99)

        said = " ".join(m["body"] for m in repo.messages.list_messages(db, 5))
        assert "$" in said
        assert "token" not in said.lower(), "the number a person acts on is the money"

    def test_it_fires_once(self, db, runner):
        from kith.infra.db import repositories as repo

        for _ in range(4):
            runner._charge_session("c1", uncached_in=1_000, cost_usd=9.99)

        notes = [m for m in repo.messages.list_messages(db, 20) if "stopped this session" in m["body"]]
        assert len(notes) == 1


class TestTheTokenFallback:
    def test_it_applies_only_when_no_cost_is_reported(self, runner, monkeypatch):
        """A local model costs nothing, so there is no money to measure and a runaway is
        bounded by volume instead."""
        from kith.services import tuning

        monkeypatch.setattr(tuning, "value", lambda k: 50_000 if k == "session_token_cap" else 300)

        runner._charge_session("c1", uncached_in=60_000, cost_usd=0.0)

        assert stopped(runner, "c1")

    def test_it_counts_what_was_read_not_what_was_re_sent(self, runner, monkeypatch):
        """Uncached, not the whole prompt. The two differ fourfold on a warm cache, and
        charging the larger one is what made the ceiling meaningless."""
        from kith.services import tuning

        monkeypatch.setattr(tuning, "value", lambda k: 1_000_000 if k == "session_token_cap" else 300)

        # A tick that re-sent millions but only read a little.
        runner._charge_session("c1", uncached_in=200_000, cost_usd=0.0)

        assert not stopped(runner, "c1")
        assert runner._session_tokens["c1"] == 200_000

    def test_a_priced_provider_never_falls_through_to_tokens(self, runner, monkeypatch):
        """Otherwise a cheap model is held to a ceiling it can never sensibly reach — the
        original bug, inverted."""
        from kith.services import tuning

        monkeypatch.setattr(tuning, "value", lambda k: 1_000 if k == "session_token_cap" else 300)

        runner._charge_session("c1", uncached_in=5_000_000, cost_usd=0.01)

        assert not stopped(runner, "c1")


class TestStartingWorkClearsIt:
    def test_keep_working_zeroes_the_money_too(self, db, runner, monkeypatch):
        from kith.infra.db import repositories as repo

        repo.conversations.create(db, "c1", "A chat", "kith-1")
        runner._session_cost["c1"] = 99.0
        runner._session_capped.add("c1")
        monkeypatch.setattr(runner, "ensure_loop", lambda: None)

        runner.keep_working("c1")

        assert runner._session_cost["c1"] == 0.0
        assert not stopped(runner, "c1")
