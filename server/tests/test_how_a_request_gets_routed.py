"""The OpenRouter routing, fallback and privacy fields a request asks for.

These are policy, resolved from settings rather than from the moment — which model to
fall back to, whether to insist on a fully-capable provider, whether to stay on
zero-data-retention hosts. Kept in a pure function so the policy can be checked without
opening a socket, the same way `_stats` is checked without a real response.
"""

from __future__ import annotations

from kith.domain.chat import Config
from kith.llm.openai_compat import _routing_options
from kith.services import tuning

PRIMARY = "openai/gpt-5.6-luna"


def _config(model: str = PRIMARY) -> Config:
    return Config(model=model, num_ctx=0, num_predict=0, system="", think=False)


class TestModelFallback:
    def test_a_fallback_model_becomes_a_models_array(self):
        # If the primary errors or is down, OpenRouter tries the next — and bills by
        # whichever actually served. The primary stays first.
        tuning.apply({"fallback_model": "moonshotai/kimi-k2"})
        assert _routing_options(_config())["models"] == [PRIMARY, "moonshotai/kimi-k2"]

    def test_no_fallback_leaves_the_request_single_model(self):
        # The default is blank, so nothing changes for anyone who hasn't set one.
        assert "models" not in _routing_options(_config())

    def test_a_fallback_equal_to_the_primary_is_not_sent(self):
        # A models array of [x, x] is just x with extra words and a wasted validation.
        tuning.apply({"fallback_model": PRIMARY})
        assert "models" not in _routing_options(_config())


def _provider(config: Config | None = None) -> dict:
    """The provider block, or an empty dict.

    Every assertion below reads a *named field* rather than the presence of this block, which is
    the mistake it used to make. `"provider" not in options` was a fine proxy for "my flag is
    off" only while every flag defaulted off — and it broke the moment provider ordering was
    given a default, reporting a failure in requiring-capable-providers and in zero-data-retention
    for a change that touched neither.
    """
    return _routing_options(config or _config()).get("provider") or {}


class TestRequiringACapableProvider:
    def test_on_by_default_after_measuring_what_off_cost(self):
        """Off, a round could be served by a host that does not do prompt caching — and four
        recorded rounds on 2026-08-03 came back with `cachedTokens: 0` on prompts of 24k-56k,
        every token billed fresh, at ~8x the usual unit price as well. Prompt caching is most of
        the economics of a long turn, so a host that silently drops it is not a cheaper host."""
        assert _provider()["require_parameters"] is True

    def test_it_can_still_be_turned_off(self):
        # The escape, for a model whose only upstream is fussy about declaring what it supports.
        tuning.apply({"require_provider_parameters": False})
        assert "require_parameters" not in _provider()


class TestZeroDataRetention:
    def test_off_by_default_so_the_provider_pool_is_not_shrunk_unasked(self):
        assert "data_collection" not in _provider()
        assert "zdr" not in _provider()

    def test_on_denies_logging_and_restricts_to_zdr_hosts(self):
        # The "runs on your machine" promise, honoured when he reaches the cloud.
        tuning.apply({"zero_data_retention": True})
        provider = _routing_options(_config())["provider"]
        assert provider["data_collection"] == "deny"
        assert provider["zdr"] is True


class TestTheOptionsReachTheWire:
    """A pure function nobody calls routes nothing. This proves stream_once sends it."""

    def test_a_configured_fallback_is_in_the_request_body(self, monkeypatch):
        from kith.llm import openai_compat

        tuning.apply({"fallback_model": "moonshotai/kimi-k2"})
        captured: dict = {}

        class FakeResp:
            status_code = 200
            encoding = "utf-8"
            text = ""

            def iter_lines(self, decode_unicode=True):
                return iter(['data: {"choices":[{"delta":{"content":"ok"}}]}', "data: [DONE]"])

            def close(self):
                pass

        def fake_post(url, json=None, headers=None, stream=None, timeout=None):
            captured["json"] = json
            return FakeResp()

        monkeypatch.setattr(openai_compat.requests, "post", fake_post)
        cfg = Config(
            model=PRIMARY,
            num_ctx=0,
            num_predict=0,
            system="",
            think=False,
            base_url="https://openrouter.ai/api/v1",
            api_key="k",
            session_id="s",  # set, so the storage-touching id lookup is skipped
        )

        list(openai_compat.stream_once([{"role": "user", "content": "hi"}], cfg))

        assert captured["json"]["models"] == [PRIMARY, "moonshotai/kimi-k2"]


class TestPinningStillWorks:
    def test_a_pinned_provider_is_a_preference_not_a_lock(self):
        # Unchanged behaviour: order the host first, but keep fallbacks so availability holds.
        tuning.apply({"openrouter_provider": "DeepInfra"})
        provider = _routing_options(_config())["provider"]
        assert provider["order"] == ["DeepInfra"]
        assert provider["allow_fallbacks"] is True

    def test_the_privacy_and_capability_flags_ride_the_same_provider_block(self):
        tuning.apply(
            {
                "openrouter_provider": "DeepInfra",
                "require_provider_parameters": True,
                "zero_data_retention": True,
            }
        )
        provider = _routing_options(_config())["provider"]
        assert provider["order"] == ["DeepInfra"]
        assert provider["require_parameters"] is True
        assert provider["data_collection"] == "deny"
