"""What else is available, and what it costs you if it isn't.

Separate from the connection because it answers a different question. A connection is
the one thing Kith cannot work without — everything here degrades him instead:

* no Docker → he can talk and search, but has no computer of his own
* no embedding model → recall matches on keywords rather than meaning
* no search → he can only read pages you hand him

So none of these block setup. Each reports what is lost and what to do about it,
because "⚠️ Docker" tells someone nothing they can act on.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import requests

from kith import settings
from kith.domain.connection import Connection
from kith.infra import sandbox
from kith.services.connections.providers import ProviderError
from kith.services.connections.providers.ollama import OllamaProvider

#: These checks run while someone waits on a screen, so they fail fast. A slow answer
#: is the same as a missing one here: both mean "carry on without it".
CHECK_TIMEOUT = (3, 6)


class Health(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"


@dataclass(frozen=True)
class Check:
    """One capability, and the consequence of its absence."""

    key: str
    title: str
    health: Health
    summary: str
    #: What to do about it. Empty when nothing needs doing.
    remedy: str = ""

    def public(self) -> dict:
        return {
            "key": self.key,
            "title": self.title,
            "health": str(self.health),
            "summary": self.summary,
            "remedy": self.remedy,
        }


def report(connection: Connection) -> list[Check]:
    """Every check, most consequential first."""
    return [_computer(), _memory(), _search(connection)]


def _computer() -> Check:
    status = sandbox.status()
    if status.get("container") == "running":
        return Check("computer", "His computer", Health.OK, "The sandbox is running.")
    if status.get("dockerAvailable"):
        return Check(
            "computer",
            "His computer",
            Health.OK,
            "Docker is available — his sandbox starts the first time he needs it.",
        )
    return Check(
        "computer",
        "His computer",
        Health.DEGRADED,
        "Docker isn't running, so he has no machine of his own.",
        "He can still think, search and remember. Start Docker Desktop to give him a "
        "shell, files, and the ability to build his own tools.",
    )


def _memory() -> Check:
    """Embeddings run locally through Ollama even when he thinks in the cloud."""
    model = settings.EMBED_MODEL
    local = Connection.local(base_url=settings.OLLAMA_HOST)
    try:
        pulled = OllamaProvider().has_model(local, model)
    except ProviderError:
        return Check(
            "memory",
            "Semantic recall",
            Health.DEGRADED,
            "Ollama isn't running, so recall matches on keywords rather than meaning.",
            f"Install Ollama and run `ollama pull {model}` whenever you like — he works fine without it.",
        )
    if pulled:
        return Check("memory", "Semantic recall", Health.OK, "He can recall by meaning.")
    return Check(
        "memory",
        "Semantic recall",
        Health.DEGRADED,
        "The embedding model isn't pulled, so recall matches on keywords.",
        f"ollama pull {model}",
    )


def _search(connection: Connection) -> Check:
    """Two ways to search, checked in the order search itself tries them."""
    if _searx_up():
        return Check(
            "search",
            "Web search",
            Health.OK,
            f"Search goes through your SearXNG at {settings.SEARCH_URL} — free.",
        )
    if connection.supports_web_plugin:
        return Check(
            "search",
            "Web search",
            Health.OK,
            "Search runs through the same key — about half a cent a search.",
        )
    return Check(
        "search",
        "Web search",
        Health.DEGRADED,
        "No search provider, so he can only read pages you point him at.",
        "OpenRouter includes search on the same key. Otherwise run a SearXNG instance "
        f"and set KITH_SEARCH_URL (currently {settings.SEARCH_URL}).",
    )


def _searx_up() -> bool:
    try:
        response = requests.get(
            f"{settings.SEARCH_URL}/search",
            params={"q": "kith", "format": "json"},
            timeout=CHECK_TIMEOUT,
        )
    except requests.exceptions.RequestException:
        return False
    return response.status_code == 200
