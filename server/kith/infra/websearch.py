"""Web search, arranged as a fallback chain so he is never blind.

Search is the one tool whose supply sits outside our control. A self-hosted
SearXNG is free and private, but it leans on public engines that CAPTCHA and
rate-limit it without warning — and when they all trip at once, a research task
starves quietly: he keeps calling ``web_search``, keeps getting nothing, and the
stall detector eventually makes him give up on a perfectly good goal.

So search tries providers cheapest-first:

1. **SearXNG** — keyless, free, unmetered. When its upstream engines are blocked
   it fails *fast* (HTTP 200 with an empty ``results`` array, in well under a
   second), which makes it a safe thing to always attempt first.
2. **OpenRouter's ``web`` plugin** — billed to the same API key that already
   powers his thinking, so there is no second account to keep alive. It is sold
   as a prompt augmenter, but the search runs *before* the model does and its
   hits come back as ``annotations`` whether or not the model cites them. So we
   ask for a single token, throw the completion away, and keep the citations —
   which turns it into a plain search API returning a title, a URL, and a real
   page excerpt (not a paraphrase) for about half a cent a call.

Which one he uses is a saved choice (see ``services.search_setup``), not a fixed
order: someone who picked SearXNG to avoid being billed should not be quietly
charged the moment their instance is rate-limited. So the chosen provider is tried
first, and the other is only a fallback when the choice was never made — the
``auto`` case, which is also what ``KITH_SEARCH_PROVIDER`` still selects.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from functools import partial
from typing import Any

import requests

from kith import settings
from kith.domain import connection
from kith.domain.chat import Config
from kith.domain.search import SearchKind, as_kind
from kith.infra.db import config_store

# Exa charges one flat fee for up to ten results and only then bills per extra
# result, so there is never a reason to ask for more than ten.
_MAX_RESULTS = 10

# Each hit carries a real excerpt of the page — often 1-5KB. That richness is the
# point (he can frequently skip a fetch_url entirely), but five of them unclipped
# would eat his context and then re-load on every tool round, so cap the snippet.
_SNIPPET_LIMIT = 1200

_PROVIDER = settings.SEARCH_PROVIDER

# Pinned rather than left to OpenRouter's default: native search varies by model
# and prices by "context size", while Exa behaves identically whatever he thinks
# with and costs a flat, predictable amount.


# The carrier model for the plugin call. Blank = whatever he thinks with, which
# is right for a cheap model; point this at a cheap slug if he ever moves to an
# expensive one, since the plugin feeds its results back through the model as
# prompt tokens (~2.5k per search) and those bill at the carrier's rate.


# Probing a blocked SearXNG costs a request each time and it is blocked for hours at a stretch,
# so paying that on *every* search is pure waste — a research turn fires dozens. Remember the
# verdict for a while and skip straight to the next provider; the TTL means it still self-heals
# once the upstream engines recover.
#
# The figure here used to be "~2s (a docker exec into the sandbox plus the request)", from when
# the search ran inside the container. It does not any more.
_SEARX_BLOCKED_TTL = 600.0
_searx_blocked_until = 0.0


def search(
    query: str, config: Config, limit: int = 5, *, engine: str = "exa", carrier: str = ""
) -> list[dict]:
    """Search the web through the first provider that actually answers.

    `config` is passed in rather than resolved here, and that is the only reason this module
    no longer imports `kith.config`. Every other function below already took it as a
    parameter — this one fetched it, which meant `infra/` importing a module that merges the
    persona with the skill index in order to find out which search provider to try first.
    The caller is `tools/web.py`, an adapter, and resolving a request's parameters is exactly
    what an adapter is for.

    `engine` and `carrier` arrive the same way and for the same reason. They were
    `tuning.value("search_engine")` and `tuning.value("search_model")`, read from inside the
    OpenRouter path — which made this file, whose whole job is to talk to search providers,
    import the settings service to find out which engine to name in a payload. The defaults
    here are the tunables' own declared defaults ("exa", and blank meaning "whatever he
    thinks with"), so a caller that passes nothing behaves exactly as before.
    """
    global _searx_blocked_until

    limit = max(1, min(int(limit or 5), _MAX_RESULTS))
    misses: list[str] = []
    # Bound per call rather than at module scope, because two of the three arguments are now
    # the caller's. `_searx` does not take them and must not be handed them, so the OpenRouter
    # one is the only entry that closes over anything.
    providers: dict[str, Callable[[str, int, Config], list[dict]]] = {
        "searx": _searx,
        "openrouter": partial(_openrouter, engine=engine, carrier=carrier),
    }

    for name in _order(config):
        if name == "searx" and time.monotonic() < _searx_blocked_until:
            misses.append("searx: skipped (blocked recently)")
            continue
        try:
            hits = providers[name](query, limit, config)
        except Exception as exc:
            if name == "searx":
                _searx_blocked_until = time.monotonic() + _SEARX_BLOCKED_TTL
            misses.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        if hits:
            if name == "searx":
                _searx_blocked_until = 0.0  # it's back — trust it again
            return hits
        misses.append(f"{name}: no results")

    # Tell him *why* it was empty. A bare "no results" reads like the web has
    # nothing on the subject, and he'd wrongly abandon the question.
    return [
        {
            "note": "Search returned nothing — "
            + "; ".join(misses)
            + ". This is a plumbing failure, not evidence the topic is empty: "
            "try fetch_url on a site you already know, or a different phrasing."
        }
    ]


def _order(config: Config) -> list[str]:
    """Which providers to try, in order.

    Read per search rather than at import, so choosing a different one in the app
    takes effect on the next search instead of the next restart.
    """
    chosen = _chosen(config)
    if chosen is SearchKind.NONE:
        return []
    if chosen is SearchKind.SEARXNG:
        return ["searx"]
    if chosen is SearchKind.OPENROUTER:
        return ["openrouter"]
    # Nobody has chosen: free first, paid as the safety net, and only when it can
    # work at all.
    return ["searx"] + (["openrouter"] if _openrouter_ready(config) else [])


def _chosen(config: Config) -> SearchKind | None:
    """The saved preference, or None when it was left on auto."""
    from kith.settings import CONFIG_DB_PATH

    if _PROVIDER and _PROVIDER != "auto":
        return as_kind(_PROVIDER)
    return as_kind(config_store.load_settings(CONFIG_DB_PATH).get(settings.SEARCH_KIND_KEY))


def _openrouter_ready(config: Config) -> bool:
    """The plugin is an OpenRouter extension; other OpenAI-compatible hosts reject
    the ``plugins`` key outright. Same endpoint test the chat client uses."""
    return bool(config.api_key) and connection.is_openrouter(config.base_url)


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #


def _searx(query: str, limit: int, config: Config) -> list[dict]:
    """Ask the instance directly.

    It used to route through ``docker exec`` into the sandbox, which made free search depend on
    Docker for no reason a user could have guessed and cost about two seconds a call. Direct
    replaced that, with the sandbox kept as a fallback for an instance published only on
    Docker's network — and then the container went entirely (`fb55331`), taking the
    `docker_available` check with it and leaving the fallback reachable only through an
    AttributeError. Unreachable is unreachable now, and says so.
    """
    url = _searx_url()
    try:
        response = requests.get(
            f"{url}/search",
            params={"q": query, "format": "json"},
            timeout=(4, 10),
        )
        response.raise_for_status()
        data = response.json()
    except (requests.exceptions.RequestException, ValueError) as direct_failure:
        # Unreachable is unreachable. There used to be a second attempt here — if Docker was
        # available, run the same search from inside the container SearXNG lived in. The
        # container went in `fb55331` ("He works on your computer now, not in a container") and
        # `docker_available` went with it; this call site was missed, so the branch raised
        # AttributeError instead of the message below, and the model was told
        # "searx: AttributeError: module ... has no attribute 'docker_available'".
        raise RuntimeError(f"SearXNG at {url} is unreachable: {direct_failure}") from None

    hits = [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": (item.get("content") or "")[:300],
        }
        for item in (data.get("results") or [])[:limit]
    ]
    if hits:
        return hits
    # It answered, but every engine behind it was blocked — a broken instance
    # masquerading as an empty web, and worth saying out loud so the caller can
    # fall through to the next provider instead of trusting the emptiness.
    raise RuntimeError(f"SearXNG at {url} returned no results for any engine")


def _searx_url() -> str:
    """The chosen instance, or the configured default."""
    from kith.settings import CONFIG_DB_PATH

    stored = str(config_store.load_settings(CONFIG_DB_PATH).get(settings.SEARCH_URL_KEY) or "")
    return (stored or settings.SEARCH_URL).rstrip("/")


def _openrouter(query: str, limit: int, config: Config, engine: str = "exa", carrier: str = "") -> list[dict]:
    if not _openrouter_ready(config):
        raise RuntimeError("no OpenRouter key configured (Settings → base URL + API key)")

    payload: dict[str, Any] = {
        "model": carrier or config.model,
        "messages": [{"role": "user", "content": query}],
        "plugins": [{"id": "web", "engine": engine, "max_results": limit}],
        # We only want the citations the plugin attaches, never the model's prose.
        # Not 1: some providers reject a max_tokens that small.
        "max_tokens": 16,
        # A reasoning model would otherwise burn its whole budget thinking about
        # an answer we are about to discard.
        "reasoning": {"enabled": False},
    }
    response = _post(config, payload)

    # Some models cannot have reasoning turned off — "Reasoning is mandatory for this endpoint
    # and cannot be disabled", HTTP 400 — and asking anyway killed search outright for anyone on
    # one of them. `google/gemini-3.7-flash` is one, so every single search 400'd and he was
    # handed the "plumbing failure" note instead of results, over and over, with no way to tell
    # from the note that his *model* was the reason.
    #
    # The transport already knew this lesson and retries without the switch (`llm/openai_compat`,
    # via the same classifier). This file duplicated the payload and not the lesson, which is the
    # actual defect: a second place that speaks the chat API, and so a second place that has to
    # know every way the chat API says no.
    #
    # Dropping the switch is the whole fix — measured against OpenRouter on that model, the retry
    # returns the same citations for the same $0.008. `max_tokens` is what makes that true: a
    # model forced to think still only gets 16 tokens to do it in, and the plugin searches
    # *before* the model runs, so the annotations are already attached to a completion that was
    # cut off after 13 tokens. Raising the ceiling to let it finish thinking buys nothing but
    # 500 billed tokens, which is why this does not.
    if response.status_code == 400 and connection.refuses_reasoning(response.text):
        payload.pop("reasoning", None)
        response = _post(config, payload)

    if response.status_code != 200:
        raise RuntimeError(f"OpenRouter search returned {response.status_code}: {response.text[:200]}")

    body = response.json()
    if body.get("error"):
        raise RuntimeError(str(body["error"])[:200])

    message = (body.get("choices") or [{}])[0].get("message") or {}
    hits = []
    for item in message.get("annotations") or []:
        if item.get("type") != "url_citation":
            continue
        citation = item.get("url_citation") or {}
        url = (citation.get("url") or "").strip()
        if not url:
            continue
        hits.append(
            {
                "title": (citation.get("title") or "").strip(),
                "url": url,
                "snippet": (citation.get("content") or "").strip()[:_SNIPPET_LIMIT],
            }
        )
    return hits[:limit]


def _post(config: Config, payload: dict[str, Any]) -> requests.Response:
    """One search request. Separate only so the retry above can be "the same thing again"."""
    return requests.post(
        f"{config.base_url.rstrip('/')}/chat/completions",
        json=payload,
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost",
            "X-Title": "Kith",
        },
        timeout=(10, 60),  # Exa fetches live pages; give it room
    )
