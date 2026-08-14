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

from kith.config import ollama_host
from kith.domain.connection import Connection
from kith.domain.search import SearchKind, SearchSetup
from kith.infra import workspace as sandbox
from kith.services import tuning
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


def report(connection: Connection, search: SearchSetup) -> list[Check]:
    """Every check, most consequential first."""
    return [_computer(), _memory(), _search(connection, search)]


def _computer() -> Check:
    """Where he works, now that it is your machine rather than a container.

    This used to report on Docker, and reported a problem whenever it was not running.
    There is nothing to report in that sense any more — the folder either exists or is
    created on first use — so the check answers the question someone actually has: where
    are his files, and what is he allowed to do with them.
    """
    from kith.infra import permissions

    status = sandbox.status()
    root = status.get("root", "")
    mode = str(status.get("mode") or permissions.Mode.ASK)
    described = {
        "ask": "He asks before touching anything outside it.",
        "auto": "He works outside it without asking; dangerous things still ask.",
        "bypass": "Nothing is checked — he can do anything you can.",
    }.get(mode, "")
    return Check(
        "computer",
        "His computer",
        Health.OK if mode != "bypass" else Health.DEGRADED,
        f"He works in {root}. {described}",
        (
            "Bypass means no gate at all. Switch to Ask or Auto in the title bar unless you meant it."
            if mode == "bypass"
            else ""
        ),
    )


def _memory() -> Check:
    """Embeddings run locally through Ollama even when he thinks in the cloud."""
    model = str(tuning.value("embed_model"))
    local = Connection.local(base_url=ollama_host())
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


def _search(connection: Connection, search: SearchSetup) -> Check:
    """Report the choice that was made, not whatever happens to be reachable.

    Checking the chosen provider rather than probing both matters: someone who picked
    SearXNG to avoid being billed should be told their instance is down, not quietly
    reassured that search "works" because a metered fallback exists.
    """
    if search.kind is SearchKind.NONE:
        return Check(
            "search",
            "Web search",
            Health.DEGRADED,
            "Search is off, so he can only read pages you point him at.",
            "Turn it on in settings whenever you like — either option works.",
        )

    if search.kind is SearchKind.OPENROUTER:
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
            "Search is set to OpenRouter, but he no longer thinks through OpenRouter.",
            "Pick a different search provider, or point him back at OpenRouter.",
        )

    if _searx_up(search.endpoint):
        return Check(
            "search",
            "Web search",
            Health.OK,
            f"Search goes through your SearXNG at {search.endpoint} — free.",
        )
    return Check(
        "search",
        "Web search",
        Health.DEGRADED,
        f"Nothing is answering at {search.endpoint}, so he can't search.",
        "Start the instance, point him at a different one, or switch to OpenRouter's "
        "search — it's about half a cent a search on the key he already has.",
    )


def _searx_up(endpoint: str) -> bool:
    try:
        response = requests.get(
            f"{endpoint}/search",
            params={"q": "kith", "format": "json"},
            timeout=CHECK_TIMEOUT,
        )
    except requests.exceptions.RequestException:
        return False
    return response.status_code == 200
