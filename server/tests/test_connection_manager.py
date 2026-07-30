"""The connection lifecycle, against a real config database and a fake provider.

The database is real because "did it actually persist" is the question. The provider
is faked because the question is what the manager does with an answer, not whether
OpenRouter is up — and a test that fails when someone's wifi drops teaches nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith.domain.connection import Connection, ModelInfo, ProviderKind
from kith.infra.db import config_store
from kith.services.connections.manager import (
    BUDGET_CEILING,
    ONBOARDED_KEY,
    ConnectionManager,
)
from kith.services.connections.providers import ProviderError


class FakeProviders:
    """Stands in for the provider registry, one canned answer at a time."""

    def __init__(self, models=(), error: str = "", lists_without_key: bool = False):
        self.models = list(models)
        self.error = error
        self.lists_without_key = lists_without_key
        self.calls = 0

    # -- the registry surface the manager uses -- #
    def for_kind(self, kind):
        return self

    def is_small(self, model_id: str) -> bool:
        from kith.services.connections.providers import is_small

        return is_small(model_id)

    def is_cloud(self, model_id: str) -> bool:
        from kith.services.connections.providers import is_cloud

        return is_cloud(model_id)

    # -- the provider surface -- #
    def list_models(self, connection):
        self.calls += 1
        if self.error:
            raise ProviderError(self.error)
        return list(self.models)


def manager_with(config_db: Path, providers) -> ConnectionManager:
    m = ConnectionManager(config_db=config_db)
    m._providers = providers
    return m


def model(mid: str, price: float | None = 1.0, ctx: int = 200_000, tools: bool | None = True):
    return ModelInfo(
        id=mid, prompt_per_mtok=price, completion_per_mtok=price, context=ctx, supports_tools=tools
    )


class TestCandidate:
    """Raw request fields becoming a domain object, exactly once, in one place."""

    def test_openrouter_endpoint_is_not_the_users_to_choose(self, config_db):
        m = ConnectionManager(config_db=config_db)
        candidate = m.candidate("openrouter", base_url="https://evil.test/v1", api_key=" k ")
        assert candidate.endpoint == "https://openrouter.ai/api/v1"
        assert candidate.api_key == "k"  # pasted keys carry whitespace

    def test_local_uses_the_address_chat_will_use(self, config_db, monkeypatch):
        # A probe that tests a different address than the chat path is worthless.
        monkeypatch.setattr("kith.settings.OLLAMA_HOST", "http://10.0.0.5:11434")
        m = ConnectionManager(config_db=config_db)
        assert m.candidate("ollama").endpoint == "http://10.0.0.5:11434"

    def test_unknown_kind_is_a_value_error(self, config_db):
        with pytest.raises(ValueError):
            ConnectionManager(config_db=config_db).candidate("mystery")


class TestOnboardedFlag:
    def test_a_fresh_install_is_not_onboarded(self, config_db):
        # The config DB seeds a default model, so completeness alone would wrongly
        # report a brand-new install as already set up.
        assert config_store.load_settings(config_db)["model"]
        assert not ConnectionManager(config_db=config_db).is_onboarded()

    def test_a_hand_configured_install_is_grandfathered(self, config_db):
        config_store.update_settings(
            config_db,
            {"model": "x/y", "base_url": "https://openrouter.ai/api/v1", "api_key": "sk-or-x"},
        )
        # No flag: this setup predates onboarding and must not be interrupted by it.
        assert ONBOARDED_KEY not in config_store.load_settings(config_db)
        assert ConnectionManager(config_db=config_db).is_onboarded()

    def test_a_local_choice_is_grandfathered_when_the_model_is_not_the_seed(self, config_db):
        config_store.update_settings(config_db, {"model": "qwen3:14b"})
        assert ConnectionManager(config_db=config_db).is_onboarded()

    def test_the_flag_cannot_outlive_a_wiped_config(self, config_db):
        config_store.update_settings(config_db, {ONBOARDED_KEY: True, "model": ""})
        assert not ConnectionManager(config_db=config_db).is_onboarded()


class TestProbe:
    def test_a_missing_key_is_asked_for_not_attempted(self, config_db):
        providers = FakeProviders(models=[model("a/b")])
        m = manager_with(config_db, providers)
        result = m.probe(m.candidate("openai", base_url="https://x.test/v1"))
        assert not result.reachable
        assert "key" in result.detail.lower()
        assert providers.calls == 0  # no point calling out with nothing to send

    def test_a_public_catalogue_is_listed_without_a_key(self, config_db):
        providers = FakeProviders(models=[model("a/b")], lists_without_key=True)
        m = manager_with(config_db, providers)
        assert m.probe(m.candidate("openrouter")).reachable

    def test_a_provider_error_becomes_a_sentence(self, config_db):
        m = manager_with(config_db, FakeProviders(error="That key was rejected."))
        result = m.probe(m.candidate("openrouter", api_key="bad"))
        assert not result.reachable
        assert result.detail == "That key was rejected."

    def test_an_unexpected_crash_still_returns_a_result(self, config_db):
        class Exploding(FakeProviders):
            def list_models(self, connection):
                raise RuntimeError("kaboom")

        # Probing happens on every keystroke; a 500 there would break onboarding.
        result = manager_with(config_db, Exploding()).probe(Connection.openrouter(api_key="k"))
        assert not result.reachable
        assert "kaboom" in result.detail

    def test_probing_never_writes(self, config_db):
        before = config_store.load_settings(config_db)
        m = manager_with(config_db, FakeProviders(models=[model("a/b")]))
        m.probe(m.candidate("openrouter", api_key="k"))
        assert config_store.load_settings(config_db) == before


class TestSuggest:
    def test_a_ladder_rather_than_three_of_the_same(self, config_db):
        catalogue = [
            model("cheap/one", price=0.01),
            model("cheap/two", price=0.02),
            model("mid/one", price=0.5),
            model("top/one", price=4.0),
        ]
        picks = ConnectionManager(config_db=config_db).suggest(ProviderKind.OPENROUTER, catalogue)
        assert picks == ["cheap/one", "mid/one", "top/one"]

    def test_dynamically_priced_routers_are_skipped(self, config_db):
        # OpenRouter reports these as unpriced; ranking them by cost is meaningless.
        catalogue = [model("openrouter/auto", price=None), model("real/model", price=1.0)]
        picks = ConnectionManager(config_db=config_db).suggest(ProviderKind.OPENROUTER, catalogue)
        assert picks == ["real/model"]

    def test_unusable_models_are_never_suggested(self, config_db):
        catalogue = [
            model("no/tools", tools=False),
            model("tiny/context", ctx=8_000),
            model("some/model:free"),
            model("some/model:batch"),
            model("good/model"),
        ]
        picks = ConnectionManager(config_db=config_db).suggest(ProviderKind.OPENROUTER, catalogue)
        assert picks == ["good/model"]

    def test_local_beats_small_beats_ollamas_cloud(self, config_db):
        catalogue = [model("glm-5.2:cloud"), model("qwen2.5:3b"), model("qwen3:14b")]
        picks = ConnectionManager(config_db=config_db).suggest(ProviderKind.OLLAMA, catalogue)
        # The card promises nothing leaves your machine, so a hosted tag comes last.
        assert picks == ["qwen3:14b", "qwen2.5:3b", "glm-5.2:cloud"]


class TestConcerns:
    def test_a_cheap_model_is_flagged_as_needing_supervision(self, config_db):
        catalogue = [model("cheap/one", price=BUDGET_CEILING / 2)]
        notes = ConnectionManager(config_db=config_db).concerns(
            Connection.openrouter(api_key="k", model="cheap/one"), tuple(catalogue)
        )
        assert any("hand work back" in note for note in notes)

    def test_a_capable_model_draws_no_complaints(self, config_db):
        catalogue = [model("good/one", price=3.0)]
        notes = ConnectionManager(config_db=config_db).concerns(
            Connection.openrouter(api_key="k", model="good/one"), tuple(catalogue)
        )
        assert notes == []

    def test_a_toolless_model_is_called_out(self, config_db):
        catalogue = [model("talker/one", tools=False)]
        notes = ConnectionManager(config_db=config_db).concerns(
            Connection.openrouter(api_key="k", model="talker/one"), tuple(catalogue)
        )
        assert any("tools" in note for note in notes)

    def test_ollamas_hosted_tags_lose_the_privacy_promise(self, config_db):
        notes = ConnectionManager(config_db=config_db).concerns(Connection.local(model="glm-5.2:cloud"), ())
        assert any("leave your machine" in note for note in notes)


class TestAdopt:
    def test_a_structural_gap_is_refused_before_any_network_call(self, config_db):
        providers = FakeProviders(models=[model("a/b")])
        m = manager_with(config_db, providers)
        with pytest.raises(ValueError, match="Choose a model"):
            m.adopt(Connection.openrouter(api_key="k"))
        assert providers.calls == 0

    def test_an_unreachable_provider_is_not_saved(self, config_db):
        m = manager_with(config_db, FakeProviders(error="That key was rejected."))
        with pytest.raises(ValueError, match="rejected"):
            m.adopt(Connection.openrouter(api_key="bad", model="a/b"))
        assert not m.is_onboarded()

    def test_a_model_the_provider_does_not_offer_is_refused(self, config_db):
        # The check that stops a stale browser tab saving a model since withdrawn.
        m = manager_with(config_db, FakeProviders(models=[model("a/b")]))
        with pytest.raises(ValueError, match="isn't offered"):
            m.adopt(Connection.openrouter(api_key="k", model="gone/model"))

    def test_adopting_persists_and_marks_onboarding_done(self, config_db):
        m = manager_with(config_db, FakeProviders(models=[model("a/b", price=3.0)]))
        saved, warnings = m.adopt(Connection.openrouter(api_key="sk-or-x", model="a/b"))

        assert saved.kind is ProviderKind.OPENROUTER
        assert saved.model == "a/b"
        assert warnings == []
        assert m.is_onboarded()
        # Survives a restart, which is the whole point of writing it.
        assert ConnectionManager(config_db=config_db).current().model == "a/b"

    def test_choosing_local_clears_the_previous_cloud_setup(self, config_db):
        cloud = manager_with(config_db, FakeProviders(models=[model("a/b")]))
        cloud.adopt(Connection.openrouter(api_key="sk-or-x", model="a/b"))

        local = manager_with(config_db, FakeProviders(models=[model("qwen3:14b")]))
        saved, _ = local.adopt(Connection.local(model="qwen3:14b"))

        # A leftover base_url and key would make infer() read this back as cloud, and
        # the old provider would keep quietly serving every request.
        assert saved.is_local
        stored = config_store.load_settings(config_db)
        assert stored["base_url"] == ""
        assert stored["api_key"] == ""

    def test_warnings_accompany_a_saved_choice_rather_than_blocking_it(self, config_db):
        m = manager_with(config_db, FakeProviders(models=[model("cheap/one", price=0.01)]))
        saved, warnings = m.adopt(Connection.openrouter(api_key="k", model="cheap/one"))
        assert saved.model == "cheap/one"  # it is the user's machine and their call
        assert warnings


class TestModelSizing:
    """The sizing heuristic, which a substring match got wrong in both directions."""

    @pytest.mark.parametrize(
        "tag",
        ["qwen3:4b", "qwen2.5:3b", "deepseek-r1:1.5b", "gemma3:270m-it-q8_0", "llama3.2:1b"],
    )
    def test_small_tags_are_recognised(self, tag):
        from kith.services.connections.providers import is_small

        assert is_small(tag)

    @pytest.mark.parametrize(
        "tag",
        [
            "qwen3:14b",  # contains "4b"
            "mistral-small:24b",  # contains "2b"
            "qwen3:32b",  # contains "2b"
            "llama3.1:8b",
            "qwen3:30b-a3b",  # 30B total, 3B active — judged on the total
            "glm-5.2:cloud",  # no size at all
            "gpt-oss:20b",
        ],
    )
    def test_large_and_unsized_tags_are_not_called_small(self, tag):
        from kith.services.connections.providers import is_small

        assert not is_small(tag)


class TestStoredKeyReuse:
    """Changing only the model must not demand the key be pasted again."""

    def test_the_saved_key_fills_a_blank(self, config_db):
        m = manager_with(config_db, FakeProviders(models=[model("a/b"), model("c/d")]))
        m.adopt(Connection.openrouter(api_key="sk-or-secret", model="a/b"))

        # A client only ever learns that a key is set, never what it is.
        assert m.candidate("openrouter", model="c/d").api_key == "sk-or-secret"

    def test_a_supplied_key_always_wins(self, config_db):
        m = manager_with(config_db, FakeProviders(models=[model("a/b")]))
        m.adopt(Connection.openrouter(api_key="sk-or-old", model="a/b"))
        assert m.candidate("openrouter", api_key="sk-or-new").api_key == "sk-or-new"

    def test_a_key_is_never_lent_to_a_different_endpoint(self, config_db):
        m = manager_with(config_db, FakeProviders(models=[model("a/b")]))
        m.adopt(Connection.openrouter(api_key="sk-or-secret", model="a/b"))
        # Otherwise /setup/probe would be a way to make Kith post the stored key
        # to any address a caller names.
        elsewhere = m.candidate("openai", base_url="https://attacker.test/v1")
        assert elsewhere.api_key == ""

    def test_switching_to_local_borrows_nothing(self, config_db):
        m = manager_with(config_db, FakeProviders(models=[model("a/b")]))
        m.adopt(Connection.openrouter(api_key="sk-or-secret", model="a/b"))
        assert m.candidate("ollama", model="qwen3:14b").api_key == ""
