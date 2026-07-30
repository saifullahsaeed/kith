"""Choosing how Kith searches: what's on offer, whether it works, and saving it.

Same shape as ``services.connections`` on purpose — ``current`` / ``options`` /
``probe`` / ``adopt`` — because it is the same problem at a smaller scale, and two
different shapes for one job is how a codebase starts needing to be memorised.

The rule is also the same: trying is not saving. ``probe`` runs a real search and
throws the results away; only ``adopt`` writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import requests

from kith import settings
from kith.domain.connection import Connection
from kith.domain.search import DEFAULT_SEARX_URL, SearchKind, SearchSetup
from kith.infra.db import config_store
from kith.services import tuning

#: Stored under the same names the environment uses, so a value means one thing
#: wherever it is read from.
KIND_KEY = "search_provider"
URL_KEY = "search_url"

#: A search anyone would recognise the results of — a probe should fail because the
#: instance is broken, never because the query was odd.
PROBE_QUERY = "wikipedia"

#: A blocked SearXNG answers fast with an empty list, so waiting longer than this
#: only delays telling someone it isn't working.
PROBE_TIMEOUT = (4, 10)


@dataclass(frozen=True)
class SearchProbe:
    """What happened when we actually searched."""

    working: bool
    detail: str = ""
    #: How many hits came back. Zero with ``working`` true means the instance
    #: answered but every engine behind it is blocked — a real and separate state.
    hits: int = 0

    def public(self) -> dict:
        return {"working": self.working, "detail": self.detail, "hits": self.hits}


@dataclass
class SearchManager:
    """Owns the stored search choice and the move to a new one."""

    config_db: Path

    # -- reading what is stored --------------------------------------------- #

    def current(self, connection: Connection) -> SearchSetup:
        """The stored choice, or the best guess when nobody has chosen yet.

        Environment first, matching how every other setting resolves here: an
        operator who sets ``KITH_SEARCH_PROVIDER`` expects it to win over a database.
        """
        stored = config_store.load_settings(self.config_db)
        url = str(stored.get(URL_KEY) or "")

        raw = settings.SEARCH_PROVIDER if settings.SEARCH_PROVIDER != "auto" else stored.get(KIND_KEY)
        kind = _as_kind(raw)
        if kind is None:
            # Nothing chosen: prefer the free option when it is there, and fall back
            # to the metered one only if he can actually use it.
            return self._guess(connection, url)
        return SearchSetup(kind=kind, searx_url=url)

    def _guess(self, connection: Connection, url: str) -> SearchSetup:
        candidate = SearchSetup.searxng(url)
        if self.probe(candidate).working:
            return candidate
        if connection.supports_web_plugin:
            return SearchSetup.openrouter()
        return SearchSetup.none()

    # -- what's on offer ----------------------------------------------------- #

    def options(self, connection: Connection) -> list[dict]:
        """The choice cards, in the order they should be preferred.

        OpenRouter's is listed but marked unavailable rather than hidden when he
        thinks elsewhere: "you can't have this, and here's why" is more useful than
        an option that silently doesn't exist.
        """
        cards = [
            {
                "kind": str(SearchKind.SEARXNG),
                "label": "SearXNG",
                "blurb": "A search instance you point at. Free and unmetered.",
                "tradeoff": (
                    "Costs nothing and no one bills you per search. It leans on public "
                    "engines that rate-limit it, so it goes quiet for hours at a time."
                ),
                "requires": "A SearXNG instance — run your own, or use a public one",
                "needsUrl": True,
                "defaultUrl": DEFAULT_SEARX_URL,
                "available": True,
                "unavailableBecause": "",
            },
            {
                "kind": str(SearchKind.OPENROUTER),
                "label": "OpenRouter search",
                "blurb": "Search on the key he already thinks with.",
                "tradeoff": (
                    "Nothing to install and it doesn't go down. About half a cent a "
                    f"search, billed to the same key — via {tuning.value('search_engine')}."
                ),
                "requires": "Nothing — it uses the key you just set up",
                "needsUrl": False,
                "defaultUrl": "",
                "available": connection.supports_web_plugin,
                "unavailableBecause": (
                    ""
                    if connection.supports_web_plugin
                    # Said as the reason it is greyed out, so it reads as an
                    # explanation rather than an error.
                    else "Only available when he thinks through OpenRouter."
                ),
            },
            {
                "kind": str(SearchKind.NONE),
                "label": "No search",
                "blurb": "He works from what you give him.",
                "tradeoff": (
                    "Nothing leaves your machine for a search. He can still read any "
                    "page you point him at, but he can't go looking on his own."
                ),
                "requires": "Nothing",
                "needsUrl": False,
                "defaultUrl": "",
                "available": True,
                "unavailableBecause": "",
            },
        ]
        return cards

    # -- trying it ----------------------------------------------------------- #

    def probe(self, setup: SearchSetup) -> SearchProbe:
        """Run a real search and throw the results away. Never raises, never writes."""
        if setup.kind is SearchKind.NONE:
            return SearchProbe(working=True, detail="He won't search.")
        if setup.kind is SearchKind.OPENROUTER:
            # Nothing to reach out to: the plugin rides the chat request, and the key
            # behind it was already checked when the connection was set up. Charging
            # someone half a cent to be told what they just proved would be rude.
            return SearchProbe(working=True, detail="Ready — it uses your OpenRouter key.")
        return self._probe_searx(setup)

    def _probe_searx(self, setup: SearchSetup) -> SearchProbe:
        """Ask the instance directly, from here.

        Directly rather than through the sandbox, which is how ``web_search`` used to
        reach it: that made free search quietly depend on Docker being installed, for
        no reason a user could have guessed.
        """
        url = f"{setup.endpoint}/search"
        try:
            response = requests.get(url, params={"q": PROBE_QUERY, "format": "json"}, timeout=PROBE_TIMEOUT)
        except requests.exceptions.ConnectionError:
            return SearchProbe(False, f"Nothing answered at {setup.endpoint}.")
        except requests.exceptions.Timeout:
            return SearchProbe(False, f"{setup.endpoint} didn't answer in time.")
        except requests.exceptions.RequestException as exc:
            return SearchProbe(False, f"Couldn't reach {setup.endpoint}. {exc}")

        if response.status_code == 403:
            return SearchProbe(
                False,
                "That instance refused the request — the JSON API is usually disabled. "
                "Add `json` to its `search.formats` setting.",
            )
        if response.status_code != 200:
            return SearchProbe(False, f"{setup.endpoint} returned {response.status_code}.")
        try:
            hits = len(response.json().get("results") or [])
        except ValueError:
            return SearchProbe(False, f"{setup.endpoint} answered, but not with JSON.")

        if hits:
            return SearchProbe(True, f"Working — {hits} results for “{PROBE_QUERY}”.", hits)
        # A well-known query returning nothing is the signature of an instance whose
        # upstream engines are all blocked. It will recover on its own, so this is a
        # warning rather than a refusal.
        return SearchProbe(
            False,
            "The instance is up but every engine behind it is blocked right now. "
            "It usually recovers within a few hours.",
        )

    # -- committing ---------------------------------------------------------- #

    def adopt(self, setup: SearchSetup, connection: Connection) -> SearchSetup:
        """Save the choice. Raises ValueError with something readable if it can't be.

        A SearXNG instance that is merely blocked is still saved: it recovers, and
        refusing the choice would force someone to pick something they don't want
        because of a temporary condition.

        The address is only kept for the option that uses it. Otherwise an address
        someone typed while comparing options would sit in the database and quietly
        become the default if they ever switched back to SearXNG.
        """
        for problem in setup.problems(connection):
            raise ValueError(problem)

        keeps_url = setup.kind is SearchKind.SEARXNG
        config_store.update_settings(
            self.config_db,
            {KIND_KEY: str(setup.kind), URL_KEY: setup.searx_url if keeps_url else ""},
        )
        return setup if keeps_url else SearchSetup(kind=setup.kind)


def _as_kind(raw: object) -> SearchKind | None:
    """Read a stored or env value, tolerating the older `searx` spelling."""
    text = str(raw or "").strip().lower()
    if not text or text == "auto":
        return None
    if text == "searx":
        return SearchKind.SEARXNG
    try:
        return SearchKind(text)
    except ValueError:
        return None
