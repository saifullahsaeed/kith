"""Feeding him a link, and finding it again — both ends of `services.sources`.

Neither end reached it. `tools/sources.py` and `api/routes/sources.py` each said
`from kith.tools import sources`, and in `tools/sources.py` that is the module *itself*: the
name bound, the attribute did not exist, and `search_sources` raised
`AttributeError: module 'kith.tools.sources' has no attribute 'search'` on every call. The
route had the same import and the same fault, so "add a source" answered 400 with an
AttributeError in the body.

The service was fine the whole time. It just had nobody pointing at it — which is also why a
reachability sweep called `services/sources.py` dead code when it was the only live part.

These tests are about wiring, so they assert against the seam rather than the behaviour: that
the tool and the route reach the module that actually implements ingest and search.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith.api.routes import sources as route
from kith.services import sources as service
from kith.tools import sources as tool


class TestTheWiring:
    def test_the_tool_reaches_the_service_not_itself(self):
        # The bug in one line: `tool.sources` used to be `tool`.
        assert tool.sources is service

    def test_the_route_reaches_the_service_not_the_tool_module(self):
        assert route.sources is service

    @pytest.mark.parametrize("name", ["search", "ingest_url", "ingest_text"])
    def test_the_service_has_what_both_of_them_call(self, name):
        assert callable(getattr(service, name))


class TestSearchingWhatYouFedHim:
    def test_it_comes_back_by_meaning(self, db: Path, monkeypatch):
        """The tool end to end, with embedding stubbed — the point is that the call completes
        and returns rows, which is exactly what it could not do."""
        monkeypatch.setattr(service, "search", lambda path, query, limit: [{"title": "a doc"}])

        out = tool.search_sources(db, {"query": "anything"})

        assert out == [{"title": "a doc"}]

    def test_it_passes_the_limit_through(self, db: Path, monkeypatch):
        seen = {}

        def fake(path, query, limit):
            seen.update(query=query, limit=limit)
            return []

        monkeypatch.setattr(service, "search", fake)

        tool.search_sources(db, {"query": "wiring", "limit": 3})

        assert seen == {"query": "wiring", "limit": 3}

    def test_no_limit_asks_for_five(self, db: Path, monkeypatch):
        seen = {}
        monkeypatch.setattr(service, "search", lambda path, query, limit: seen.update(limit=limit))

        tool.search_sources(db, {"query": "wiring"})

        assert seen == {"limit": 5}
