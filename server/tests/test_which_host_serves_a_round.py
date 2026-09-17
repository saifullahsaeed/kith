"""`sort: "price"` is honoured and still picks the wrong host, twice over.

Measured over 428 recorded rounds of `deepseek/deepseek-v4.1-flash` on this install. The pool
`sort` produced was genuinely the six cheapest of the model's nineteen hosts — so the setting
reaches the wire and works — but within that pool:

  * the three hosts it calls a tie at $0.150 read cached prompt tokens at $0.015, $0.015 and
    $0.003. 78.8% of this install's prompt tokens are cache reads, so they are not a tie: one
    of them is five times cheaper on the rate that actually bills.
  * it has no tie-break at all. Of those three, the 11 tok/s host served 86 rounds and the
    146 tok/s host served 8.

Two keys — effective price, then throughput — and no OpenRouter sort expresses both. So the
order is worked out here and sent as `provider.order`. `throughput` and `latency` get the same
treatment with the keys swapped, because the gap is symmetric. See `kith/domain/endpoints.py`.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from kith.domain import endpoints as policy
from kith.domain.chat import Config, Routing
from kith.llm.openai_compat import _routing_options
from kith.services import tuning


def _endpoint(slug: str, prompt: float, cache: float, tps: float, **rest) -> policy.Endpoint:
    return policy.Endpoint(
        slug=slug,
        name=slug.title(),
        prompt_per_mtok=prompt,
        completion_per_mtok=prompt * 4,
        cache_read_per_mtok=cache,
        throughput=tps,
        **rest,
    )


#: The real table, the night this was found.
THE_SIX = [
    _endpoint("alibaba", 0.150, 0.0150, 93),
    _endpoint("relace/fp4", 0.150, 0.0150, 47),
    _endpoint("deepseek", 0.150, 0.0030, 87),
    _endpoint("deepinfra/fp8", 0.200, 0.0060, 35),
    _endpoint("morph/fp8", 0.210, 0.0063, 13),
    _endpoint("fireworks", 0.220, 0.0070, 79),
]


class TestTheCacheRateDecidesTheBill:
    def test_the_cheap_cache_wins_a_tie_on_the_headline_rate(self):
        """All three quote $0.150. One of them reads cache at a fifth of the others."""
        assert policy.rank(THE_SIX)[0] == "deepseek"

    def test_an_unpriced_host_is_never_treated_as_free(self):
        """A missing price must read as unknown. Treated as zero it wins every band it is in."""
        blank = policy.from_api({"tag": "mystery", "pricing": {}})
        assert policy.effective_price(blank) == float("inf")


class TestSpeedBreaksTies:
    def test_the_slow_host_of_an_equal_pair_goes_second(self):
        pair = [_endpoint("slow", 0.150, 0.0150, 13), _endpoint("quick", 0.150, 0.0150, 146)]
        assert policy.rank(pair) == ("quick", "slow")

    def test_speed_never_outranks_a_real_price_difference(self):
        """A host twice the price does not buy its way up by being fast."""
        pair = [_endpoint("dear", 0.400, 0.0400, 300), _endpoint("cheap", 0.150, 0.0030, 20)]
        assert policy.rank(pair) == ("cheap", "dear")

    def test_latency_settles_a_throughput_tie(self):
        pair = [
            _endpoint("sluggish", 0.150, 0.0150, 90, latency_ms=2787),
            _endpoint("prompt", 0.150, 0.0150, 90, latency_ms=896),
        ]
        assert policy.rank(pair) == ("prompt", "sluggish")


class TestAskingForSpeedInstead:
    """The same gap, facing the other way: OpenRouter's throughput sort has no price tie-break."""

    def test_throughput_leads_and_price_settles_the_tie(self):
        """Reka and GMICloud sit beside DeepSeek on speed at roughly double the money."""
        pool = [
            _endpoint("reka/fp4", 0.290, 0.0290, 110),
            _endpoint("gmicloud/fp8", 0.285, 0.0057, 89),
            _endpoint("deepseek", 0.150, 0.0030, 87),
        ]
        ranked = policy.rank(pool, "throughput")
        assert ranked[0] == "reka/fp4"
        # 89 and 87 tok/s are within the tie window, so the cheaper of the two goes first.
        assert ranked[1] == "deepseek"

    def test_price_never_outranks_a_real_speed_difference(self):
        """Asking for throughput says speed matters more, not that price is all that matters."""
        pair = [_endpoint("cheap", 0.150, 0.0030, 20), _endpoint("quick", 0.400, 0.0400, 300)]
        assert policy.rank(pair, "throughput") == ("quick", "cheap")

    def test_latency_leads_when_that_is_what_was_asked(self):
        pool = [
            _endpoint("dear", 0.400, 0.0400, 90, latency_ms=400),
            _endpoint("slow_start", 0.150, 0.0030, 90, latency_ms=2787),
        ]
        assert policy.rank(pool, "latency")[0] == "dear"

    def test_an_unmeasured_host_is_not_treated_as_instant(self):
        """A missing latency is `inf`, not 0 — 0 would front every band it landed in."""
        assert policy.from_api({"tag": "mystery"}).latency_ms == float("inf")

    def test_an_unmeasured_host_is_not_treated_as_infinitely_fast(self):
        pair = [_endpoint("measured", 0.150, 0.0030, 87), _endpoint("unknown", 0.150, 0.0030, 0)]
        assert policy.rank(pair, "throughput") == ("measured", "unknown")

    def test_an_unknown_ordering_falls_back_to_price(self):
        assert policy.rank(THE_SIX, "pirce")[0] == "deepseek"


class TestHealth:
    def test_a_deranked_host_sinks_without_being_dropped(self):
        """Dropping it is a decision about availability; this one is about preference."""
        pool = [_endpoint("broken", 0.100, 0.0010, 200, status=-2), _endpoint("fine", 0.300, 0.0300, 50)]
        assert policy.rank(pool) == ("fine", "broken")

    def test_a_flapping_host_does_not_lead(self):
        pool = [_endpoint("flaky", 0.100, 0.0010, 200, uptime=86.6), _endpoint("steady", 0.300, 0.0300, 50)]
        assert policy.rank(pool) == ("steady", "flaky")

    def test_a_host_listed_twice_is_ordered_once(self):
        """`baseten/fp8` really does appear twice on this model. A repeat in `order` is a list
        that says something we did not mean."""
        pool = [
            _endpoint("baseten/fp8", 0.300, 0.0300, 78),
            _endpoint("together", 0.300, 0.0060, 141),
            _endpoint("baseten/fp8", 0.300, 0.0300, 69),
        ]
        assert policy.rank(pool) == ("together", "baseten/fp8")

    def test_an_unreadable_row_is_not_ordered(self):
        """An empty slug in the list is a filter that matches nothing."""
        assert policy.rank([policy.from_api({}), _endpoint("deepseek", 0.150, 0.0030, 97)]) == ("deepseek",)

    def test_every_host_is_listed(self):
        """A short list means the fallback past its end is unranked again."""
        assert len(policy.rank(THE_SIX)) == len(THE_SIX)


class TestReadingTheApi:
    ROW: ClassVar[dict] = {
        "tag": "deepseek",
        "provider_name": "DeepSeek",
        "pricing": {"prompt": "0.00000015", "completion": "0.0000006", "input_cache_read": "0.000000003"},
        "throughput_last_30m": {"p50": 87},
        "latency_last_30m": {"p50": 896},
        "status": 0,
        "uptime_last_30m": 99.99,
    }

    def test_prices_arrive_per_token_and_are_kept_per_million(self):
        one = policy.from_api(self.ROW)
        assert one.prompt_per_mtok == pytest.approx(0.15)
        assert one.cache_read_per_mtok == pytest.approx(0.003)

    def test_the_slug_is_what_order_takes(self):
        assert policy.from_api(self.ROW).slug == "deepseek"

    def test_a_row_missing_everything_does_not_raise(self):
        assert policy.from_api({}).slug == ""


class TestItReachesThePayload:
    def _provider(self, routing: Routing) -> dict:
        config = Config(
            model="deepseek/deepseek-v4.1-flash", num_ctx=40960, num_predict=-1, system="", think=False
        )
        return _routing_options(config, routing).get("provider") or {}

    def test_an_order_is_sent_with_fallbacks_on(self):
        out = self._provider(Routing(order=("deepseek", "alibaba")))
        assert out["order"] == ["deepseek", "alibaba"]
        assert out["allow_fallbacks"] is True

    def test_it_supersedes_sort(self):
        """Both together is contradictory, and `sort` is the one that gets it wrong."""
        assert "sort" not in self._provider(Routing(prefer_by="price", order=("deepseek",)))

    def test_without_one_the_old_sort_still_ships(self):
        """A failed fetch must cost the refinement, not the routing."""
        assert self._provider(Routing(prefer_by="price"))["sort"] == "price"

    def test_a_pin_still_wins(self):
        out = self._provider(Routing(pinned="fireworks", order=("deepseek",)))
        assert out["order"] == ["fireworks"]


class TestWhenTheOrderIsWorkedOut:
    def setup_method(self):
        from kith.services import routing as routing_policy

        routing_policy.forget()

    def _config(self, **rest) -> Config:
        return Config(
            model="deepseek/deepseek-v4.1-flash",
            num_ctx=40960,
            num_predict=-1,
            system="",
            think=False,
            base_url="https://openrouter.ai/api/v1",
            api_key="sk-or-test",
            **rest,
        )

    def test_a_pin_is_not_second_guessed(self, monkeypatch):
        from kith.services import routing as routing_policy

        monkeypatch.setattr(routing_policy, "_fetch", lambda config, by: ("deepseek",))
        tuning.apply({"openrouter_provider": "fireworks"})
        assert routing_policy.resolved(self._config()).order == ()

    def test_throughput_is_ranked_here_too(self, monkeypatch):
        """The gap is symmetric: OpenRouter's throughput sort has no price tie-break either."""
        from kith.services import routing as routing_policy

        monkeypatch.setattr(routing_policy, "_fetch", lambda config, by: (by,))
        tuning.apply({"openrouter_provider": "", "prefer_provider_by": "throughput"})
        assert routing_policy.resolved(self._config()).order == ("throughput",)

    def test_blank_is_left_to_openrouter(self, monkeypatch):
        """A real answer -- "use your own balancing" -- and not ours to override."""
        from kith.services import routing as routing_policy

        monkeypatch.setattr(routing_policy, "_fetch", lambda config, by: ("deepseek",))
        tuning.apply({"openrouter_provider": "", "prefer_provider_by": ""})
        assert routing_policy.resolved(self._config()).order == ()

    def test_switching_ordering_does_not_reuse_the_other_order(self, monkeypatch):
        """One table, two rankings. A cache keyed on the model alone hands back the wrong one."""
        from kith.services import routing as routing_policy

        monkeypatch.setattr(routing_policy, "_fetch", lambda config, by: (by,))
        tuning.apply({"openrouter_provider": "", "prefer_provider_by": "price"})
        assert routing_policy.resolved(self._config()).order == ("price",)
        tuning.apply({"prefer_provider_by": "latency"})
        assert routing_policy.resolved(self._config()).order == ("latency",)

    def test_price_gets_the_worked_out_order(self, monkeypatch):
        from kith.services import routing as routing_policy

        monkeypatch.setattr(routing_policy, "_fetch", lambda config, by: ("deepseek", "alibaba"))
        tuning.apply({"openrouter_provider": "", "prefer_provider_by": "price"})
        assert routing_policy.resolved(self._config()).order == ("deepseek", "alibaba")

    def test_a_plain_openai_endpoint_is_left_alone(self, monkeypatch):
        """`provider` is an OpenRouter extension and other hosts 400 on the whole request."""
        from kith.services import routing as routing_policy

        monkeypatch.setattr(routing_policy, "_fetch", lambda config, by: ("deepseek",))
        tuning.apply({"openrouter_provider": "", "prefer_provider_by": "price"})
        plain = self._config()
        assert routing_policy.resolved(replace_url(plain, "https://api.openai.com/v1")).order == ()

    def test_the_table_is_fetched_once_per_model(self, monkeypatch):
        """A turn asks every round; prices move on the scale of minutes."""
        from kith.services import routing as routing_policy

        calls = []
        monkeypatch.setattr(
            routing_policy, "_fetch", lambda config, by: calls.append(config.model) or ("deepseek",)
        )
        tuning.apply({"openrouter_provider": "", "prefer_provider_by": "price"})
        for _ in range(5):
            routing_policy.resolved(self._config())
        assert calls == ["deepseek/deepseek-v4.1-flash"]

    def test_a_failed_fetch_falls_back_to_sort(self, monkeypatch):
        from kith.services import routing as routing_policy

        monkeypatch.setattr(routing_policy, "_fetch", lambda config, by: ())
        tuning.apply({"openrouter_provider": "", "prefer_provider_by": "price"})
        resolved = routing_policy.resolved(self._config())
        assert resolved.order == ()
        assert resolved.prefer_by == "price"


def replace_url(config: Config, url: str) -> Config:
    from dataclasses import replace

    return replace(config, base_url=url)


class TestSettledOnceNotPerRound:
    """A conversation picks its hosts when it starts and then stays there.

    A prompt cache lives on one upstream. Measured over 1,386 recorded rounds: a round that
    stayed put read 94.6% of its prompt from cache at $0.0064, and the first round after a host
    change read 68.9% at $0.0177 — 2.75x, on a smaller prompt. Hosts publish time-of-day rates,
    so a ranking refreshed on a timer would keep finding a marginally cheaper host and keep
    paying that 2.75x to move to it. Ranking is what a *new* conversation does.
    """

    def setup_method(self):
        from kith.services import routing as routing_policy

        routing_policy.forget()

    def _db(self, tmp_path):
        from kith.infra.db import migrations
        from kith.infra.db.repositories import conversations as repo

        path = tmp_path / "agent.db"
        migrations.init(path)
        repo.create(path, "c1", "A conversation", "kith-abc")
        return path

    def _config(self) -> Config:
        return Config(
            model="deepseek/deepseek-v4.1-flash",
            num_ctx=40960,
            num_predict=-1,
            system="",
            think=False,
            base_url="https://openrouter.ai/api/v1",
            api_key="sk-or-test",
        )

    def test_the_first_turn_ranks_and_writes_it_down(self, tmp_path, monkeypatch):
        from kith.services import conversations
        from kith.services import routing as routing_policy

        db = self._db(tmp_path)
        monkeypatch.setattr(routing_policy, "_fetch", lambda config, by: ("deepseek", "alibaba"))
        tuning.apply({"openrouter_provider": "", "prefer_provider_by": "price"})
        assert routing_policy.for_conversation(self._config(), db, "c1").order == ("deepseek", "alibaba")
        assert conversations.provider_order(db, "c1") == ("deepseek", "alibaba")

    def test_later_turns_read_the_row_and_never_re_rank(self, tmp_path, monkeypatch):
        """The network call is the tell: a second fetch means a second chance to move."""
        from kith.services import routing as routing_policy

        db = self._db(tmp_path)
        calls = []

        def fetch(config, by):
            calls.append(by)
            return ("deepseek", "alibaba")

        monkeypatch.setattr(routing_policy, "_fetch", fetch)
        tuning.apply({"openrouter_provider": "", "prefer_provider_by": "price"})
        for _ in range(4):
            routing_policy.for_conversation(self._config(), db, "c1")
        assert calls == ["price"]

    def test_a_cheaper_host_appearing_later_does_not_move_it(self, tmp_path, monkeypatch):
        """Prices move through the day. Chasing them is what costs the cold prefix."""
        from kith.services import routing as routing_policy

        db = self._db(tmp_path)
        monkeypatch.setattr(routing_policy, "_fetch", lambda config, by: ("alibaba",))
        tuning.apply({"openrouter_provider": "", "prefer_provider_by": "price"})
        routing_policy.for_conversation(self._config(), db, "c1")
        monkeypatch.setattr(routing_policy, "_fetch", lambda config, by: ("deepseek",))
        assert routing_policy.for_conversation(self._config(), db, "c1").order == ("alibaba",)

    def test_a_failed_first_fetch_is_retried_not_written_blank(self, tmp_path, monkeypatch):
        from kith.services import conversations
        from kith.services import routing as routing_policy

        db = self._db(tmp_path)
        monkeypatch.setattr(routing_policy, "_fetch", lambda config, by: ())
        tuning.apply({"openrouter_provider": "", "prefer_provider_by": "price"})
        assert routing_policy.for_conversation(self._config(), db, "c1").order == ()
        assert conversations.provider_order(db, "c1") == ()
        monkeypatch.setattr(routing_policy, "_fetch", lambda config, by: ("deepseek",))
        assert routing_policy.for_conversation(self._config(), db, "c1").order == ("deepseek",)

    def test_a_second_writer_does_not_move_a_warm_conversation(self, tmp_path):
        """Two turns of one conversation can reach this together."""
        from kith.services import conversations

        db = self._db(tmp_path)
        conversations.remember_provider_order(db, "c1", ("deepseek",))
        conversations.remember_provider_order(db, "c1", ("morph/fp8",))
        assert conversations.provider_order(db, "c1") == ("deepseek",)

    def test_a_pin_is_still_not_second_guessed(self, tmp_path, monkeypatch):
        from kith.services import routing as routing_policy

        db = self._db(tmp_path)
        monkeypatch.setattr(routing_policy, "_fetch", lambda config, by: ("deepseek",))
        tuning.apply({"openrouter_provider": "fireworks"})
        assert routing_policy.for_conversation(self._config(), db, "c1").order == ()
