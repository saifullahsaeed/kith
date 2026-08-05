"""One model on OpenRouter is many hosts at many prices, and nothing was choosing between them.

Measured across 1,071 recorded rounds of the same `openai/gpt-5.6-luna`, restricted to
input-dominated rounds so the response length cannot skew the rate:

    usual                     $0.13 per million uncached prompt tokens
    2026-08-03 15:54–15:58    $1.01–1.10   (and `cachedTokens: 0` on four of them)
    2026-08-01 23:24–23:30    $4.12–6.59

Those are contiguous blocks at one rate, not scattered outliers — which is the signature of the
mechanism that replaced pinning. `openrouter_provider` was deliberately left blank because the
session id asks OpenRouter for the same upstream, keeping a turn's rounds on one warm cache. That
works. What it does not do is care *which* upstream: a session that opens on an expensive host
stays there for its whole life. Stickiness without a preference is stickiness to an accident.

Three gaps, one test class each: nothing ordered the pool by price, nothing capped what a round
could cost, and `require_parameters` was off so a host that does not do prompt caching was a
legal destination — which is how a 56k-token prompt came back billed entirely fresh.

And the reason none of it was visible: the provider that served a round was never recorded, so a
bill could not be attributed to a host. OpenRouter sends it on every chunk.
"""

from __future__ import annotations

from kith.config import Config
from kith.llm.openai_compat import _routing_options, _stats
from kith.services import tuning


def _config(model: str = "openai/gpt-5.6-luna") -> Config:
    return Config(model=model, num_ctx=1_050_000, num_predict=-1, system="", think=False)


def _provider(**settings) -> dict:
    tuning.apply(settings)
    return _routing_options(_config()).get("provider") or {}


class TestPreferringACheapHost:
    def test_price_is_the_default(self):
        """Blank was the old default and it is what let a session sit on a 50x host."""
        assert tuning.value("prefer_provider_by") == "price"

    def test_it_reaches_the_payload(self):
        assert _provider(prefer_provider_by="price")["sort"] == "price"

    def test_another_ordering_is_honoured(self):
        """Someone who cares about speed more than price should be able to say so."""
        assert _provider(prefer_provider_by="throughput")["sort"] == "throughput"

    def test_blank_leaves_routing_to_openrouter(self):
        assert "sort" not in _provider(prefer_provider_by="")

    def test_an_explicit_pin_wins_over_ordering(self):
        """`order` and `sort` together is contradictory — the person named a host, so give them
        that host rather than quietly ordering around them."""
        out = _provider(prefer_provider_by="price", openrouter_provider="fireworks")
        assert out["order"] == ["fireworks"]
        assert "sort" not in out
        assert out["allow_fallbacks"] is True


class TestTheCeiling:
    def test_it_is_off_by_default(self):
        """The right figure is per-model, and one set too low takes the model off the air."""
        assert tuning.value("max_prompt_price") == 0.0
        assert "max_price" not in _provider(max_prompt_price=0.0)

    def test_a_ceiling_reaches_the_payload(self):
        assert _provider(max_prompt_price=0.5)["max_price"] == {"prompt": 0.5}

    def test_it_would_have_excluded_the_expensive_rounds(self):
        """The measured rates: usual $0.13, the bad blocks $1.01 and $6.59. A ceiling anywhere
        between keeps the first and refuses the others."""
        ceiling = _provider(max_prompt_price=0.5)["max_price"]["prompt"]
        assert 0.13 < ceiling < 1.01


class TestNotPayingForACacheWeDoNotGet:
    def test_requiring_full_capability_is_now_the_default(self):
        """Off, a round may be served by a host that does not do prompt caching. Four recorded
        rounds came back with `cachedTokens: 0` on prompts of 24k–56k tokens."""
        assert tuning.value("require_provider_parameters") is True
        assert _provider(require_provider_parameters=True)["require_parameters"] is True

    def test_it_can_still_be_switched_off(self):
        """The 'goes dark' escape, for a model whose only host is fussy about declaring support."""
        assert "require_parameters" not in _provider(require_provider_parameters=False)


class TestRecordingWhoServedIt:
    def test_the_provider_is_carried_on_the_stats_row(self):
        """Without this a bill cannot be attributed to an upstream, which is why fifty-fold
        price variation ran for days without anyone being able to name the cause."""
        row = _stats({"prompt_tokens": 1_000, "completion_tokens": 10}, 1.0, "m", "fireworks")
        assert row["provider"] == "fireworks"

    def test_a_host_that_does_not_say_leaves_it_blank(self):
        """A plain OpenAI-compatible endpoint sends no provider field, and a guess here would be
        worse than a gap — it would be attributing spend to the wrong host."""
        assert _stats({"prompt_tokens": 1_000}, 1.0, "m")["provider"] == ""

    def test_the_cost_and_the_cache_split_are_on_the_same_row(self):
        """The three numbers only mean something together: cost alone cannot tell an expensive
        host from a lost cache, and on 2026-08-03 both were happening at once.

        Run through `measured`, which is where `uncachedTokens` is derived — `_stats` does not
        carry it, and asserting on the transport's row alone would have been testing half the
        path that every surface actually reads.
        """
        from kith.services.agent_loop import measured

        row = measured(
            _stats(
                {
                    "prompt_tokens": 55_874,
                    "completion_tokens": 40,
                    "prompt_tokens_details": {"cached_tokens": 0},
                    "cost": 0.05627,
                },
                2.0,
                "openai/gpt-5.6-luna",
                "some-host",
            )
        )
        assert row["cachedTokens"] == 0
        assert row["uncachedTokens"] == 55_874
        assert row["costUsd"] == 0.05627
        assert row["provider"] == "some-host"
        # $1.01 per million uncached prompt tokens — the measured bad rate, reconstructible
        # from the row alone, which is the point.
        assert 1.00 <= row["costUsd"] / row["uncachedTokens"] * 1e6 <= 1.02
