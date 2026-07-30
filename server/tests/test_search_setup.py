"""Choosing how he searches.

The interesting cases are all about *not* being helpful in the wrong way: not
offering an option that cannot work, not silently billing someone who chose free, and
not refusing a good choice because of a condition that will pass on its own.
"""

from __future__ import annotations

import pytest

from kith.domain.connection import Connection
from kith.domain.search import DEFAULT_SEARX_URL, SearchKind, SearchSetup
from kith.infra.db import config_store
from kith.services.search_setup import KIND_KEY, URL_KEY, SearchManager, SearchProbe

OPENROUTER = Connection.openrouter(api_key="sk-or-x", model="a/b")
OTHER_CLOUD = Connection(
    kind=Connection.infer("https://nim.test/v1", "k", "m").kind,
    model="m",
    base_url="https://nim.test/v1",
    api_key="k",
)
LOCAL = Connection.local(model="qwen3:14b")


class TestSetupType:
    def test_the_plugin_only_exists_where_it_works(self):
        plugin = SearchSetup.openrouter()
        assert plugin.available_with(OPENROUTER)
        # Other OpenAI-compatible hosts reject the whole request rather than
        # ignoring the `plugins` key, so this is broken rather than merely absent.
        assert not plugin.available_with(OTHER_CLOUD)
        assert not plugin.available_with(LOCAL)

    def test_searxng_works_wherever_he_thinks(self):
        assert SearchSetup.searxng().available_with(LOCAL)
        assert SearchSetup.searxng().available_with(OPENROUTER)

    def test_a_blank_address_means_the_default(self):
        assert SearchSetup.searxng().endpoint == DEFAULT_SEARX_URL
        assert SearchSetup.searxng("http://box.local:8888/").endpoint == "http://box.local:8888"

    def test_only_one_option_costs_money(self):
        assert SearchSetup.openrouter().costs_money
        assert not SearchSetup.searxng().costs_money
        assert not SearchSetup.none().costs_money

    def test_an_impossible_choice_says_why(self):
        problems = SearchSetup.openrouter().problems(LOCAL)
        assert problems and "thinks through OpenRouter" in problems[0]

    def test_a_nonsense_address_is_refused(self):
        assert SearchSetup.searxng("localhost:8888").problems(LOCAL)


class TestOptions:
    def test_the_plugin_is_shown_greyed_out_rather_than_hidden(self, config_db):
        cards = {c["kind"]: c for c in SearchManager(config_db=config_db).options(LOCAL)}
        plugin = cards["openrouter"]
        # Hiding it would leave someone wondering where the option went.
        assert not plugin["available"]
        assert "OpenRouter" in plugin["unavailableBecause"]

    def test_every_option_is_offered_on_openrouter(self, config_db):
        cards = SearchManager(config_db=config_db).options(OPENROUTER)
        assert [c["kind"] for c in cards] == ["searxng", "openrouter", "none"]
        assert all(c["available"] for c in cards)

    def test_only_searxng_asks_for_an_address(self, config_db):
        cards = {c["kind"]: c for c in SearchManager(config_db=config_db).options(OPENROUTER)}
        assert cards["searxng"]["needsUrl"]
        assert not cards["openrouter"]["needsUrl"]
        assert not cards["none"]["needsUrl"]


class TestProbe:
    def test_no_search_needs_no_checking(self, config_db):
        assert SearchManager(config_db=config_db).probe(SearchSetup.none()).working

    def test_the_plugin_is_not_charged_just_to_test_it(self, config_db, monkeypatch):
        # A probe that spent half a cent to confirm what the connection step already
        # proved would be billing someone for reassurance.
        def explode(*args, **kwargs):
            raise AssertionError("probing the plugin must not make a request")

        monkeypatch.setattr("kith.services.search_setup.requests.get", explode)
        assert SearchManager(config_db=config_db).probe(SearchSetup.openrouter()).working

    def test_a_working_instance_reports_its_hits(self, config_db, monkeypatch):
        monkeypatch.setattr(
            "kith.services.search_setup.requests.get",
            lambda *a, **k: _Response(200, {"results": [{"url": "x"}, {"url": "y"}]}),
        )
        result = SearchManager(config_db=config_db).probe(SearchSetup.searxng())
        assert result.working
        assert result.hits == 2

    def test_an_instance_with_every_engine_blocked_is_not_working(self, config_db, monkeypatch):
        # HTTP 200 with an empty list is a broken instance masquerading as an empty
        # web. Reporting it as working would send someone off with silent search.
        monkeypatch.setattr(
            "kith.services.search_setup.requests.get",
            lambda *a, **k: _Response(200, {"results": []}),
        )
        result = SearchManager(config_db=config_db).probe(SearchSetup.searxng())
        assert not result.working
        assert "blocked" in result.detail

    def test_a_disabled_json_api_says_how_to_fix_it(self, config_db, monkeypatch):
        # By far the most common SearXNG misconfiguration, and unguessable.
        monkeypatch.setattr("kith.services.search_setup.requests.get", lambda *a, **k: _Response(403, {}))
        result = SearchManager(config_db=config_db).probe(SearchSetup.searxng())
        assert not result.working
        assert "search.formats" in result.detail

    def test_an_unreachable_instance_names_the_address(self, config_db, monkeypatch):
        import requests

        def refuse(*args, **kwargs):
            raise requests.exceptions.ConnectionError()

        monkeypatch.setattr("kith.services.search_setup.requests.get", refuse)
        result = SearchManager(config_db=config_db).probe(SearchSetup.searxng("http://box:9"))
        assert not result.working
        assert "http://box:9" in result.detail


class TestCurrent:
    def test_a_saved_choice_is_honoured(self, config_db):
        config_store.update_settings(config_db, {KIND_KEY: "searxng", URL_KEY: "http://box.local:8888"})
        current = SearchManager(config_db=config_db).current(OPENROUTER)
        assert current.kind is SearchKind.SEARXNG
        assert current.endpoint == "http://box.local:8888"

    def test_the_environment_wins_over_the_database(self, config_db, monkeypatch):
        config_store.update_settings(config_db, {KIND_KEY: "none"})
        monkeypatch.setattr("kith.settings.SEARCH_PROVIDER", "openrouter")
        assert SearchManager(config_db=config_db).current(OPENROUTER).kind is SearchKind.OPENROUTER

    def test_the_old_searx_spelling_still_reads(self, config_db, monkeypatch):
        # KITH_SEARCH_PROVIDER=searx predates this type and is presumably in someone's
        # shell profile.
        monkeypatch.setattr("kith.settings.SEARCH_PROVIDER", "searx")
        assert SearchManager(config_db=config_db).current(LOCAL).kind is SearchKind.SEARXNG

    def test_with_nothing_chosen_the_free_option_is_preferred(self, config_db, monkeypatch):
        monkeypatch.setattr(SearchManager, "probe", lambda self, setup: SearchProbe(True))
        assert SearchManager(config_db=config_db).current(OPENROUTER).kind is SearchKind.SEARXNG

    def test_it_falls_back_to_the_paid_one_only_when_it_can(self, config_db, monkeypatch):
        monkeypatch.setattr(SearchManager, "probe", lambda self, setup: SearchProbe(False))
        assert SearchManager(config_db=config_db).current(OPENROUTER).kind is SearchKind.OPENROUTER
        # Nowhere to fall back to, so say so rather than implying search works.
        assert SearchManager(config_db=config_db).current(LOCAL).kind is SearchKind.NONE


class TestAdopt:
    def test_saving_survives_a_restart(self, config_db):
        manager = SearchManager(config_db=config_db)
        manager.adopt(SearchSetup.searxng("http://box.local:8888"), LOCAL)

        reloaded = SearchManager(config_db=config_db).current(LOCAL)
        assert reloaded.kind is SearchKind.SEARXNG
        assert reloaded.endpoint == "http://box.local:8888"

    def test_an_impossible_choice_is_refused(self, config_db):
        with pytest.raises(ValueError, match="thinks through OpenRouter"):
            SearchManager(config_db=config_db).adopt(SearchSetup.openrouter(), LOCAL)

    def test_a_merely_blocked_instance_is_still_accepted(self, config_db, monkeypatch):
        # It recovers on its own. Refusing would push someone onto a metered provider
        # they didn't want because of a condition that passes in a few hours.
        monkeypatch.setattr(SearchManager, "probe", lambda self, setup: SearchProbe(False))
        manager = SearchManager(config_db=config_db)
        saved = manager.adopt(SearchSetup.searxng(), LOCAL)
        assert saved.kind is SearchKind.SEARXNG


class _Response:
    """The two things the probe asks of a response."""

    def __init__(self, status: int, body: dict):
        self.status_code = status
        self._body = body

    def json(self) -> dict:
        return self._body


class TestStaleAddress:
    def test_an_address_is_not_kept_for_an_option_that_ignores_it(self, config_db):
        manager = SearchManager(config_db=config_db)
        # Typed while comparing options, then a different option was chosen.
        saved = manager.adopt(SearchSetup(kind=SearchKind.OPENROUTER, searx_url="http://dead:9"), OPENROUTER)

        assert saved.searx_url == ""
        assert config_store.load_settings(config_db)[URL_KEY] == ""
        # Otherwise switching back to SearXNG later would silently reuse the dead one.
        config_store.update_settings(config_db, {KIND_KEY: "searxng"})
        assert SearchManager(config_db=config_db).current(OPENROUTER).endpoint == DEFAULT_SEARX_URL
