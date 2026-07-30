"""The OpenRouter provider, against canned SDK responses.

The SDK is not exercised over the network here — that would test OpenRouter's uptime.
What matters is the translation: catalogue entries into something a picker can rank,
and the shapes that have to be got right because they are easy to get wrong quietly.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kith.domain.connection import Connection
from kith.services.connections.providers.openrouter import (
    OpenRouterProvider,
    _per_million,
    _to_model_info,
)


def entry(
    mid: str = "vendor/model",
    prompt: str = "0.000005",
    completion: str = "0.000025",
    context: int = 200_000,
    params: tuple[str, ...] = ("tools", "temperature"),
    benchmarks: object = None,
    hugging_face_id: str | None = None,
    expiration_date: str | None = None,
) -> SimpleNamespace:
    """One catalogue entry, shaped like the SDK's model object."""
    return SimpleNamespace(
        id=mid,
        name=f"Vendor: {mid}",
        pricing=SimpleNamespace(prompt=prompt, completion=completion),
        context_length=context,
        supported_parameters=list(params),
        benchmarks=benchmarks,
        hugging_face_id=hugging_face_id,
        expiration_date=expiration_date,
    )


class TestPricing:
    def test_per_token_strings_become_dollars_per_million(self):
        assert _per_million("0.000005") == 5.0
        assert _per_million("0.00000003") == 0.03

    def test_dynamic_pricing_is_unknown_not_negative(self):
        # OpenRouter encodes "it depends" as -1 per token for its routing models.
        # Passed through, that becomes -$1,000,000/Mtok and sorts first everywhere.
        assert _per_million("-1") is None

    def test_free_is_zero_and_missing_is_none(self):
        assert _per_million("0") == 0.0
        assert _per_million(None) is None
        assert _per_million("not a number") is None


class TestBenchmarks:
    def test_scores_are_read_from_an_object(self):
        scores = SimpleNamespace(agentic_index=55.3, coding_index=78.0)
        block = SimpleNamespace(artificial_analysis=scores)
        block.artificial_analysis.model_dump = lambda: {"agentic_index": 55.3, "coding_index": 78.0}
        model = _to_model_info(entry(benchmarks=block))
        assert model.agentic_index == 55.3
        assert model.coding_index == 78.0

    def test_scores_are_read_from_a_dict(self):
        # The field is loosely typed and has been seen both ways.
        block = {"artificial_analysis": {"agentic_index": 31.1, "coding_index": 56.2}}
        model = _to_model_info(entry(benchmarks=block))
        assert model.agentic_index == 31.1

    def test_an_unmeasured_model_is_none_not_zero(self):
        # Zero would rank it last among scored models rather than as unknown, which
        # is a claim the catalogue never made.
        assert _to_model_info(entry(benchmarks=None)).agentic_index is None
        assert _to_model_info(entry(benchmarks={})).agentic_index is None


class TestTranslation:
    def test_published_weights_are_taken_from_the_hugging_face_id(self):
        assert _to_model_info(entry(hugging_face_id="deepseek-ai/V4")).open_weights
        assert not _to_model_info(entry(hugging_face_id=None)).open_weights

    def test_tool_support_comes_from_supported_parameters(self):
        assert _to_model_info(entry(params=("tools",))).supports_tools is True
        assert _to_model_info(entry(params=("temperature",))).supports_tools is False

    def test_a_withdrawal_date_is_carried_through(self):
        model = _to_model_info(entry(expiration_date="2026-08-10"))
        assert model.retires_on == "2026-08-10"
        # And that alone stops it being recommended.
        assert not model.is_recommendable


class TestCatalogue:
    def test_batch_only_models_are_dropped_entirely(self, monkeypatch):
        """They 404 on a normal chat call, so listing one is offering a trap.

        Worse than useless: a `:batch` twin is cheaper than the model it duplicates, so
        it reads as the better deal right up until his first message fails.
        """
        listed = [entry("vendor/model"), entry("vendor/model:batch", prompt="0.0000025")]
        page = SimpleNamespace(result=SimpleNamespace(data=listed))
        monkeypatch.setattr(OpenRouterProvider, "_call", staticmethod(lambda *a, **k: page))

        models = OpenRouterProvider().list_models(Connection.openrouter(api_key="k"))
        assert [m.id for m in models] == ["vendor/model"]

    def test_toolless_models_are_listed_but_flagged(self, monkeypatch):
        # Hiding them turns "this can't do what you need" into "this doesn't exist",
        # and someone searching for a model by name deserves to be told why.
        page = SimpleNamespace(result=SimpleNamespace(data=[entry("vendor/talker", params=())]))
        monkeypatch.setattr(OpenRouterProvider, "_call", staticmethod(lambda *a, **k: page))

        models = OpenRouterProvider().list_models(Connection.openrouter(api_key="k"))
        assert len(models) == 1
        assert models[0].supports_tools is False


class TestClient:
    @pytest.mark.parametrize(
        ("key", "expected"),
        [("", None), ("sk-or-real", "sk-or-real")],
    )
    def test_a_blank_key_becomes_no_credential_at_all(self, key, expected):
        """An empty string is not the same as absent, here.

        Given ``api_key=""`` the SDK builds the header anyway and httpx refuses it
        locally with ``Illegal header value b'Bearer '`` — which would break listing
        the public catalogue before a key exists, the thing that lets the model picker
        fill while someone is still deciding.
        """
        client = OpenRouterProvider._client(Connection.openrouter(api_key=key))
        try:
            security = client.sdk_configuration.security
            assert (security.api_key if security else None) == expected
        finally:
            client.__exit__(None, None, None)
