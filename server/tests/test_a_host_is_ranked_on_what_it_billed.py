"""The hosts were ranked on their quotes, and the bills disagreed.

Measured Sep 13-27 on `deepseek/deepseek-v4.1-flash`, from the `stats` event every round writes
to its transcript. Stored orders put Together first and DeepSeek ninth to thirteenth. Together
billed $38.38 over 2,531 rounds, $0.044 per million prompt tokens; DeepSeek billed $19.40 over
5,724, $0.009. No price list says why — Together served 87.6% of prompt tokens from cache
against DeepSeek's 96.5%, and lost the cache mid-turn on 8.0% of rounds against 1.4% — but the
bills say how much, and they were on disk the whole time.

So a host with enough rounds on record ranks on its own bill, a host without them ranks on its
quote scaled onto the bills' scale, and every order stored before this is ranked again, once.
"""

from __future__ import annotations

import statistics

import pytest

from kith.domain import endpoints as policy
from kith.domain.chat import Config
from kith.infra.db import migrations
from kith.infra.db.connection import apply_migrations, connect
from kith.services import routing, tuning

MODEL = "deepseek/deepseek-v4.1-flash"


def _endpoint(slug: str, prompt: float, cache: float, tps: float = 80, **rest) -> policy.Endpoint:
    return policy.Endpoint(
        slug=slug,
        name=rest.pop("name", slug.split("/")[0].title()),
        prompt_per_mtok=prompt,
        completion_per_mtok=prompt * 4,
        cache_read_per_mtok=cache,
        throughput=tps,
        **rest,
    )


def _bill(provider: str, per_mtok: float, rounds: int = 1000, cache_share: float = 0.9) -> policy.Bill:
    """A bill of exactly `per_mtok`, over a million tokens of traffic with no reply in it."""
    return policy.Bill(
        provider=provider,
        rounds=rounds,
        prompt_tokens=1_000_000,
        cached_tokens=int(1_000_000 * cache_share),
        completion_tokens=0,
        cost_usd=per_mtok,
    )


#: The two that mattered, as quoted and as billed. Together's quote is the cheaper one.
TOGETHER = _endpoint("together", 0.100, 0.0010, name="Together")
DEEPSEEK = _endpoint("deepseek", 0.150, 0.0030, name="DeepSeek")
BILLED = [
    policy.Bill("Together", 2531, 867_000_000, 760_000_000, 2_000_000, 38.38),
    policy.Bill("DeepSeek", 5724, 2_150_000_000, 2_075_000_000, 4_500_000, 19.40),
]


class TestTheBillOutranksTheQuote:
    def test_the_cheaper_in_practice_host_leads(self):
        """On quotes Together leads. On what they billed, DeepSeek does, by nearly five times."""
        assert policy.rank([TOGETHER, DEEPSEEK])[0] == "together"
        assert policy.rank(policy.priced([TOGETHER, DEEPSEEK], BILLED)) == ("deepseek", "together")

    def test_the_figure_is_the_bill_itself(self):
        priced = {one.slug: one for one in policy.priced([TOGETHER, DEEPSEEK], BILLED)}
        assert priced["together"].cost_per_mtok == pytest.approx(38.38 / 869_000_000 * 1e6)
        assert priced["deepseek"].billed_rounds == 5724

    def test_a_handful_of_rounds_is_not_yet_a_bill(self):
        """A run of cold rounds is enough to make any host look dear, and a host demoted on
        that stops getting the conversations that would clear it."""
        thin = [
            _bill("Together", 0.001, rounds=policy.MIN_ROUNDS - 1),
            _bill("DeepSeek", 0.5, rounds=policy.MIN_ROUNDS - 1),
        ]
        priced = policy.priced([TOGETHER, DEEPSEEK], thin)
        assert all(one.billed_rounds == 0 for one in priced)
        assert policy.rank(priced)[0] == "together"

    def test_a_bill_names_the_host_and_the_table_names_the_slug(self):
        """A round reports "DeepInfra"; the table's slug is `deepinfra/fp8`. The name is matched
        first and the slug's host part when there is no name, so either spelling finds it."""
        named = _endpoint("deepinfra/fp8", 0.2, 0.006, name="DeepInfra")
        nameless = _endpoint("open-inference/fp4", 0.1, 0.01, name="")
        priced = policy.priced([named, nameless], [_bill("DeepInfra", 0.025), _bill("OpenInference", 0.011)])
        assert [one.billed_rounds for one in priced] == [1000, 1000]

    def test_two_rows_from_one_host_share_its_bill(self):
        """A round does not say which of a host's deployments served it."""
        twice = [
            _endpoint("baseten/fp8", 0.3, 0.03, name="Baseten"),
            _endpoint("baseten/fp8", 0.3, 0.03, name="Baseten"),
        ]
        priced = policy.priced(twice, [_bill("Baseten", 0.02)])
        assert [one.cost_per_mtok for one in priced] == pytest.approx([0.02, 0.02])


class TestAnUnbilledHostStillHasAPlace:
    def test_its_quote_is_scaled_by_how_far_bills_sat_from_quotes(self):
        """The factor is the median of the billed hosts' bill-to-quote ratios: what an ordinary
        host does, so the newcomer is assumed ordinary rather than perfect."""
        a = _endpoint("a", 0.2, 0.01)  # quotes 0.1 * 0.2 + 0.9 * 0.01 = 0.029 at a 90% cache share
        b = _endpoint("b", 0.1, 0.005)  # quotes 0.0145
        c = _endpoint("c", 0.3, 0.001)  # quotes 0.0309, and has no bill
        bills = [_bill("A", 0.058), _bill("B", 0.0145)]  # ratios 2.0 and 1.0
        priced = {one.slug: one for one in policy.priced([a, b, c], bills)}
        assert priced["c"].cost_per_mtok == pytest.approx(0.0309 * statistics.median([2.0, 1.0]))
        assert priced["c"].billed_rounds == 0
        assert policy.rank(list(priced.values())) == ("b", "c", "a")

    def test_a_much_cheaper_quote_still_gets_its_chance(self):
        """Ranking on bills alone would never send a new host a round, so it could never earn
        one. A quote well below every bill leads; a dear one goes last; neither is dropped."""
        cheap = _endpoint("cheapnew", 0.01, 0.0001)
        dear = _endpoint("dearnew", 2.0, 0.2)
        ranked = policy.rank(policy.priced([TOGETHER, DEEPSEEK, cheap, dear], BILLED))
        assert ranked == ("cheapnew", "deepseek", "together", "dearnew")

    def test_the_traffic_shape_is_measured_not_assumed(self):
        """`CACHE_HIT_SHARE` guessed 79% of prompt tokens were cache reads. On this model's
        rounds it was 90%, and at 96% a host that is dear to miss and cheap to
        read beats one that is the other way round — the order the guess gets backwards."""
        misses_dear = _endpoint("missesdear", 0.30, 0.003)
        reads_dear = _endpoint("readsdear", 0.15, 0.030)
        assert policy.rank([misses_dear, reads_dear])[0] == "readsdear"
        elsewhere = [_bill("Someone", 0.02, rounds=10, cache_share=0.96)]
        assert policy.rank(policy.priced([misses_dear, reads_dear], elsewhere))[0] == "missesdear"

    def test_with_no_bills_at_all_the_ranking_is_the_one_before(self):
        table = [TOGETHER, DEEPSEEK, _endpoint("alibaba", 0.15, 0.015), _endpoint("morph/fp8", 0.21, 0.0063)]
        assert policy.rank(policy.priced(table, [])) == policy.rank(table)


class TestTheOtherPreferencesStillHold:
    def test_a_four_bit_host_never_leads_however_little_it_billed(self):
        """`open-inference/fp4` billed $0.011 — as cheap as DeepSeek — and lost the thread in
        33 of 43 long rounds. Cheap was how it got them."""
        fp4 = _endpoint("open-inference/fp4", 0.1, 0.01, name="OpenInference", quantization="fp4")
        bills = [*BILLED, _bill("OpenInference", 0.001)]
        assert policy.rank(policy.priced([fp4, TOGETHER, DEEPSEEK], bills))[-1] == "open-inference/fp4"

    def test_an_unhealthy_host_sinks_whatever_it_billed(self):
        down = _endpoint("deepseek", 0.15, 0.003, name="DeepSeek", status=-2)
        assert policy.rank(policy.priced([down, TOGETHER], BILLED)) == ("together", "deepseek")

    def test_asking_for_speed_still_gets_speed_with_the_bill_settling_ties(self):
        quick = _endpoint("together", 0.1, 0.001, tps=300, name="Together")
        level = [
            _endpoint("together", 0.1, 0.001, tps=90, name="Together"),
            _endpoint("deepseek", 0.15, 0.003, tps=88, name="DeepSeek"),
        ]
        assert policy.rank(policy.priced([quick, DEEPSEEK], BILLED), "throughput")[0] == "together"
        assert policy.rank(policy.priced(level, BILLED), "throughput")[0] == "deepseek"


class TestThePickerShowsTheBill:
    def test_the_price_shown_is_the_bill_it_ranked_on(self):
        shown = policy.priced([DEEPSEEK], BILLED)[0].public()
        assert shown["effectivePerMtok"] == pytest.approx(19.40 / 2_154_500_000 * 1e6)
        assert shown["billedRounds"] == 5724

    def test_an_estimate_says_it_is_one(self):
        assert policy.priced([_endpoint("newhost", 0.1, 0.001)], BILLED)[0].public()["billedRounds"] == 0


def _config() -> Config:
    return Config(
        model=MODEL,
        num_ctx=40960,
        num_predict=-1,
        system="",
        think=False,
        base_url="https://openrouter.ai/api/v1",
        api_key="sk-or-test",
    )


class TestTheRouterReadsTheRecord:
    def test_the_table_is_priced_from_what_was_recorded(self, db, monkeypatch):
        """End to end below the network: the endpoint table off the wire, the bills out of the
        install's database, and DeepSeek first although Together quotes less."""
        import kith.settings
        from kith.infra.db import repositories as repo

        monkeypatch.setattr(kith.settings, "AGENT_DB_PATH", db)
        repo.host_costs.fill(
            db,
            [
                {
                    "model": MODEL,
                    "provider": one.provider,
                    "rounds": one.rounds,
                    "prompt_tokens": one.prompt_tokens,
                    "cached_tokens": one.cached_tokens,
                    "completion_tokens": one.completion_tokens,
                    "cost_usd": one.cost_usd,
                }
                for one in BILLED
            ],
        )
        monkeypatch.setattr(routing, "_rows", lambda config: [TOGETHER, DEEPSEEK])
        assert [one.slug for one in routing.table(_config(), "price")] == ["deepseek", "together"]
        assert routing.table(_config(), "price")[0].billed_rounds == 5724


class TestAnOrderRankedOnQuotesIsRankedAgainOnce:
    """Every stored order predates the bills, so every one of them is a quote ranking — the one
    that put Together first. Each is ranked again on its conversation's next turn, and the new
    order is then kept exactly as any order is: moving a warm conversation costs a cold prefix."""

    def _upgraded(self, tmp_path):
        """A database as it stood before this change, holding the order the quotes produced,
        then upgraded the way a restart upgrades it."""
        path = tmp_path / "agent.db"
        conn = connect(path)
        try:
            apply_migrations(conn, migrations._migrations()[:51])
            conn.execute(
                "INSERT INTO conversations (id, title, session_id, created_at, updated_at, messages,"
                " provider_order, provider_model) VALUES ('c1', 'old', 'kith-abc', '2026-09-25', '2026-09-27', 40,"
                " 'together,krea/fp8,novita/fp8,deepseek', ?)",
                (MODEL,),
            )
            conn.commit()
        finally:
            conn.close()
        migrations.init(path)
        return path

    def _counting(self, monkeypatch) -> list[str]:
        calls: list[str] = []
        monkeypatch.setattr(
            routing, "_fetch", lambda config, by: calls.append(by) or ("deepseek", "together")
        )
        tuning.apply({"openrouter_provider": "", "prefer_provider_by": "price"})
        return calls

    def test_the_old_order_is_not_what_the_next_turn_gets(self, tmp_path, monkeypatch):
        db = self._upgraded(tmp_path)
        calls = self._counting(monkeypatch)
        assert routing.for_conversation(_config(), db, "c1").order == ("deepseek", "together")
        assert calls == ["price"]

    def test_the_new_order_is_then_kept(self, tmp_path, monkeypatch):
        from kith.services import conversations

        db = self._upgraded(tmp_path)
        calls = self._counting(monkeypatch)
        for _ in range(3):
            routing.for_conversation(_config(), db, "c1")
        assert calls == ["price"]
        assert conversations.provider_order(db, "c1", MODEL) == ("deepseek", "together")

    def test_a_restart_does_not_rank_it_a_third_time(self, tmp_path, monkeypatch):
        """Once, not every boot: the clearing is a migration, and a migration runs once."""
        db = self._upgraded(tmp_path)
        calls = self._counting(monkeypatch)
        routing.for_conversation(_config(), db, "c1")
        migrations.init(db)
        routing.for_conversation(_config(), db, "c1")
        assert calls == ["price"]
