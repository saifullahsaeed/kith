"""The pin was settable and unanswerable.

`openrouter_provider` takes a provider slug — `deepinfra`, `morph/fp8` — and until now no
screen in Kith printed one. So using it meant knowing the slug from OpenRouter's own site and
spelling it right first time, and a wrong answer does not fail: `provider.order` with an
unrecognised name is ignored, fallbacks serve the round, and the setting reads back exactly as
typed while doing nothing. A setting that looks applied and is not is worse than no setting.

What was missing was never the routing — that has worked since `domain/endpoints.py` — but the
three facts needed to answer it: which hosts there are, what each one costs on the rate that
actually bills, and which one served the last round. These are the tests for the first two;
the third is one field on a receipt that was already on the wire.
"""

from __future__ import annotations

import pytest
from apiflask import APIFlask

from kith.api.routes import api
from kith.domain import endpoints as policy
from kith.domain.chat import Config, Routing
from kith.services import routing


def _row(tag: str, **rest) -> dict:
    """One row shaped as OpenRouter's `/models/{id}/endpoints` sends it."""
    row = {
        "tag": tag,
        "provider_name": tag.split("/")[0].title(),
        "pricing": {"prompt": "0.00000015", "completion": "0.0000006", "input_cache_read": "0.000000015"},
        "throughput_last_30m": {"p50": 80},
        "latency_last_30m": {"p50": 400},
        "uptime_last_30m": 99.9,
        "status": 0,
    }
    row.update(rest)
    return row


class TestTheTableSaysWhatARowActuallyIs:
    def test_quantization_survives_the_read(self):
        """The column that is usually the whole difference between the cheapest row and a good
        one — same model, same name, bf16 on one host and int4 on the next — and nothing ranked
        on it or showed it, so it may as well not have been sent."""
        assert policy.from_api(_row("deepinfra/fp8", quantization="fp8")).quantization == "fp8"

    def test_a_host_that_truncates_the_model_says_so(self):
        """A 256k model is routinely served by hosts capped far below it, and the round that
        overflows has been the first anyone heard of it."""
        endpoint = policy.from_api(_row("cheap", context_length=32768, max_completion_tokens=4096))
        assert endpoint.context_length == 32768
        assert endpoint.max_completion_tokens == 4096

    def test_an_unstated_quantization_is_blank_rather_than_a_guess(self):
        assert policy.from_api(_row("mystery")).quantization == ""


class TestThePickerAndTheRouterAgree:
    def test_the_price_shown_is_the_price_ranked_on(self):
        """The headline rate is not the one that bills — around four fifths of this install's
        prompt tokens are cache reads. A picker that displayed one figure while the router
        ordered by another would be a new way to be wrong about the same question."""
        endpoint = policy.from_api(_row("deepseek"))
        assert endpoint.public()["effectivePerMtok"] == pytest.approx(policy.effective_price(endpoint))

    def test_an_unquoted_price_crosses_as_nothing_rather_than_as_infinity(self):
        """`inf` is how this module says "not quoted", and JSON has no word for it — `Infinity`
        is not valid JSON and parsers disagree about it. Null reads as a dash; a host that
        published no price must never arrive looking free."""
        shown = policy.from_api({"tag": "mystery", "pricing": {}}).public()
        assert shown["promptPerMtok"] is None
        assert shown["effectivePerMtok"] is None

    def test_a_host_below_the_uptime_floor_is_shown_and_marked(self):
        """Taking it off the list would be lying about what can happen: fallbacks still reach
        it. It simply never leads, and the row says why."""
        assert policy.from_api(_row("flaky", uptime_last_30m=80.0)).public()["healthy"] is False


class TestTheListIsTheOneTheRouterWouldUse:
    @staticmethod
    def _serving(monkeypatch, rows: list[dict]) -> None:
        class Answer:
            status_code = 200

            @staticmethod
            def json() -> dict:
                return {"data": {"endpoints": rows}}

        monkeypatch.setattr(routing.requests, "get", lambda *a, **k: Answer())

    def test_hosts_come_back_in_the_order_rounds_would_be_tried_in(self, monkeypatch):
        """Ranked, so a pin is visibly an override of a decision and not one made in a vacuum."""
        self._serving(
            monkeypatch,
            [
                _row("dear", pricing={"prompt": "0.000002", "completion": "0.000008", "input_cache_read": "0.0000002"}),
                _row("cheap"),
            ],
        )
        order = [one.slug for one in routing.table(_config(), "price")]
        assert order == ["cheap", "dear"]

    def test_blank_leaves_the_order_as_openrouter_sent_it(self, monkeypatch):
        """Blank is a real answer — "leave it to OpenRouter's own balancing" — and a screen that
        reordered the list anyway would be showing a preference nobody expressed."""
        self._serving(monkeypatch, [_row("second"), _row("first")])
        assert [one.slug for one in routing.table(_config(), "")] == ["second", "first"]

    def test_a_table_that_cannot_be_read_is_an_empty_list_and_not_an_error(self, monkeypatch):
        """Same trade the router already makes: not knowing costs the old behaviour, and a
        settings screen that 500s because a host list was slow is worse than one saying so."""
        def refuse(*_args, **_kwargs):
            raise routing.requests.exceptions.ConnectionError("no network")

        monkeypatch.setattr(routing.requests, "get", refuse)
        assert routing.table(_config(), "price") == []


class TestAPinThatDoesNothingSaysSo:
    """The failure this whole surface exists to end. Hosts are per-model and the pin is
    install-wide, so a slug that serves one model frequently serves none of the next — and
    OpenRouter's silence about it is what let the setting sit there looking live."""

    def test_a_pin_naming_a_host_that_does_not_serve_this_model_is_reported_dead(self, monkeypatch):
        body = _ask(monkeypatch, pinned="deepinfra", slugs=["alibaba", "fireworks"])
        assert body["pinnedServesModel"] is False

    def test_a_pin_the_model_honours_is_left_alone(self, monkeypatch):
        body = _ask(monkeypatch, pinned="fireworks", slugs=["alibaba", "fireworks"])
        assert body["pinnedServesModel"] is True

    def test_no_pin_is_not_a_broken_pin(self, monkeypatch):
        """Blank is the default and the recommendation. It must not wear a warning."""
        body = _ask(monkeypatch, pinned="", slugs=["alibaba"])
        assert body["pinnedServesModel"] is True

    def test_a_connection_that_is_not_openrouter_has_nothing_to_pin_to(self, monkeypatch):
        """Different sentence from "the list is empty", and the screen words them differently:
        one endpoint already serves every round."""
        body = _ask(monkeypatch, pinned="", slugs=["alibaba"], base_url="https://api.openai.com/v1")
        assert body["supported"] is False
        assert body["rows"] == []


def _config(base_url: str = "https://openrouter.ai/api/v1") -> Config:
    return Config(
        model="deepseek/deepseek-v4.1-flash",
        num_ctx=8192,
        num_predict=8192,
        system="",
        think=False,
        base_url=base_url,
        api_key="sk-test",
    )


def _ask(monkeypatch, *, pinned: str, slugs: list[str], base_url: str = "https://openrouter.ai/api/v1") -> dict:
    """The route, on its own app so the token gate is not what is under test here."""
    from kith.api.routes import endpoints as route

    monkeypatch.setattr(route, "default_config", lambda: _config(base_url))
    monkeypatch.setattr(route.tuning, "routing", lambda: Routing(pinned=pinned, prefer_by="price"))
    monkeypatch.setattr(
        route.routing, "table", lambda *_a, **_k: [policy.from_api(_row(slug)) for slug in slugs]
    )

    app = APIFlask(__name__)
    app.config.update(TESTING=True)
    app.register_blueprint(api, url_prefix="/api")
    return app.test_client().get("/api/models/endpoints").get_json()
