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
    created: object = 1784912544,
    description: str = "A model.",
    knowledge_cutoff: str = "2026-02-16",
    max_output: int | None = None,
) -> SimpleNamespace:
    """One catalogue entry, shaped like the SDK's model object."""
    return SimpleNamespace(
        id=mid,
        name=f"Vendor: {mid}",
        pricing=SimpleNamespace(prompt=prompt, completion=completion),
        context_length=context,
        top_provider=SimpleNamespace(max_completion_tokens=max_output),
        supported_parameters=list(params),
        benchmarks=benchmarks,
        hugging_face_id=hugging_face_id,
        expiration_date=expiration_date,
        created=created,
        description=description,
        knowledge_cutoff=knowledge_cutoff,
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


class TestWhatThePickerShows:
    """The fields the picker needs to answer a question without sending you elsewhere.

    All of these were already arriving in the catalogue and being dropped on the floor —
    the model list carried one score, a context length and two prices, so "which of these
    should I try today" was a question you had to leave the app to answer. The point of
    reading them is that nobody has to.
    """

    def test_the_three_indices_are_all_kept(self):
        """Coding, agentic and general ability rank differently, so one is not the three."""
        block = {
            "artificial_analysis": {
                "agentic_index": 59.2,
                "coding_index": 78.0,
                "intelligence_index": 63.1,
            }
        }
        model = _to_model_info(entry(benchmarks=block))
        assert (model.coding_index, model.agentic_index, model.intelligence_index) == (
            78.0,
            59.2,
            63.1,
        )

    def test_the_best_arena_placing_is_the_one_kept(self):
        """First at data visualisation and fortieth at gamedev is worth knowing;
        the average of the two says neither thing."""
        block = SimpleNamespace(
            design_arena=[
                SimpleNamespace(arena="models", category="gamedev", elo=1100.0, rank=40, win_rate=41.0),
                SimpleNamespace(arena="models", category="dataviz", elo=1383.0, rank=1, win_rate=63.9),
            ],
            artificial_analysis=None,
        )
        model = _to_model_info(entry(benchmarks=block))
        assert model.arena_rank == 1
        assert model.arena_category == "models/dataviz"
        assert model.arena_win_rate == 63.9

    def test_a_model_with_no_placing_says_so(self):
        block = SimpleNamespace(design_arena=[], artificial_analysis=None)
        assert _to_model_info(entry(benchmarks=block)).arena_rank is None
        assert _to_model_info(entry()).public()["arena"] is None

    def test_the_release_timestamp_becomes_a_date(self):
        """1784912544 does not answer "is this new" to anybody.

        Read in UTC, deliberately: a release date that shifted with the reader's
        timezone would have two people disagree about when a model came out, and being
        a day out either side of midnight costs nothing next to that.
        """
        model = _to_model_info(entry(created=1784912544))
        assert model.released_on == "2026-07-24"

    def test_a_broken_timestamp_is_no_date_rather_than_a_crash(self):
        assert _to_model_info(entry(created="whenever")).released_on is None
        assert _to_model_info(entry(created=None)).released_on is None

    def test_the_descriptive_fields_survive_being_absent(self):
        """They are shown around a model rather than ranked on, so a renamed key
        upstream must not take the whole catalogue down with it."""
        bare = SimpleNamespace(
            id="vendor/model",
            name="Vendor: model",
            pricing=SimpleNamespace(prompt="0.000005", completion="0.000025"),
            context_length=200_000,
            supported_parameters=["tools"],
            benchmarks=None,
            hugging_face_id=None,
            expiration_date=None,
        )
        model = _to_model_info(bare)
        assert model.description == ""
        assert model.knowledge_cutoff == ""
        assert model.released_on is None
        assert model.max_output is None

    def test_max_output_is_not_the_context_window(self):
        """The number that stops a long file halfway through, which the context length
        does not tell you."""
        model = _to_model_info(entry(context=1_000_000, max_output=128_000))
        assert model.context == 1_000_000
        assert model.max_output == 128_000


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
