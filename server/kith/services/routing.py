"""The provider order for a conversation, settled once and then left alone.

`tuning.routing()` answers "what did he set", which is a settings question and stays where the
other settings are. This answers "given that, which hosts in which order", which needs the
model's endpoint table off the network — so it lives a layer up, and hands the transport a
finished `Routing` exactly as the pinned-provider path already does.

**Once per conversation, not once per round.** A prompt cache lives on one upstream, so the
round that changes host throws the whole prefix away: measured over 1,386 recorded rounds, a
round that stayed put read 94.6% of its prompt from cache at $0.0064, and the first round after
a host change read 68.9% at $0.0177. Prices here move through the day — hosts publish
time-of-day rates — so a ranking refreshed on a timer would keep discovering a marginally
cheaper host and keep paying 2.75x to move to it. Ranking is therefore something a *new*
conversation does; an existing one reads what it already decided, and reaches no network at all.

The install-wide path — a step nobody is working, a history fold — has no conversation to
remember anything on, so it ranks against a short-lived cache instead.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace
from pathlib import Path

import requests

from kith.domain import endpoints as endpoint_policy
from kith.domain.chat import Config, Routing
from kith.domain.connection import is_openrouter
from kith.services import tuning

log = logging.getLogger(__name__)

#: How long a fetched order stays good. Prices move on promotions and health on outages, and
#: neither is a per-round event; re-asking every round would put an HTTP round trip in front of
#: every model round trip to learn nothing.
_TTL_SECONDS = 600

#: Short, because this sits in front of a turn that cannot start until it returns. A slow
#: answer here is worse than no answer: no answer costs the old `sort` behaviour for ten
#: minutes, a slow one costs the person watching a blank screen.
_TIMEOUT_SECONDS = 6

_cache: dict[str, tuple[float, tuple[str, ...]]] = {}
_lock = threading.Lock()


def for_conversation(config: Config, agent_db: Path, conversation_id: str) -> Routing:
    """Settings routing, plus the host order this conversation settled on when it began.

    The first turn of a conversation ranks and writes the answer down; every turn after it
    reads that row. A conversation that predates the column, or one started while the endpoint
    table could not be read, has nothing stored and ranks again next turn — which is the right
    behaviour and not a retry loop, because the write happens as soon as one succeeds.
    """
    base = tuning.routing()
    if not _rankable(base, config) or not conversation_id:
        return resolved(config) if not conversation_id else base

    from kith.services import conversations

    settled = conversations.provider_order(agent_db, conversation_id)
    if settled:
        return replace(base, order=settled)

    order = _fetch(config, base.prefer_by)
    if not order:
        return base
    conversations.remember_provider_order(agent_db, conversation_id, order)
    return replace(base, order=order)


def _rankable(base: Routing, config: Config) -> bool:
    """Is this a request we are allowed to, and able to, rank the hosts for?"""
    if base.pinned:
        # He named a host. Ordering around that is answering a question he did not ask.
        return False
    if base.prefer_by not in endpoint_policy.ORDERINGS:
        # Blank is a real answer — "leave it to OpenRouter's own balancing" — and not ours to
        # override. Anything else is a value the settings coercion should already have refused.
        return False
    return bool(config.api_key and is_openrouter(config.base_url))


def resolved(config: Config) -> Routing:
    """The same, for a request that belongs to no conversation.

    A history fold, a step nobody is working. There is no row to settle the decision on, so it
    is settled against a short-lived cache instead — which is the right trade here and would be
    the wrong one for a turn: these are one-shot requests with no prefix to keep warm.

    Deliberately total, like `for_conversation`: it always returns a usable `Routing`, and the
    worst case is the `sort` that shipped before any of this.
    """
    base = tuning.routing()
    if not _rankable(base, config):
        return base
    order = _order_for(config, base.prefer_by)
    return replace(base, order=order) if order else base


def _order_for(config: Config, by: str) -> tuple[str, ...]:
    # Keyed on both, because the same table ranks differently per ordering and a cache keyed on
    # the model alone would hand a price order to someone who had just switched to throughput.
    key = f"{config.model}\n{by}"
    with _lock:
        cached = _cache.get(key)
        if cached and time.time() - cached[0] < _TTL_SECONDS:
            return cached[1]
    order = _fetch(config, by)
    with _lock:
        # Cached even when empty, so a model with no endpoint table — anything that is not an
        # OpenRouter-hosted slug — is not re-asked every round for the whole of a long turn.
        _cache[key] = (time.time(), order)
    return order


def _fetch(config: Config, by: str) -> tuple[str, ...]:
    url = f"{config.base_url.rstrip('/')}/models/{config.model}/endpoints"
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            return ()
        rows = ((response.json() or {}).get("data") or {}).get("endpoints") or []
    except (requests.exceptions.RequestException, ValueError) as error:
        # Logged rather than raised. The consequence of not knowing is "route the way we routed
        # last month", and taking a turn down over a ranking refinement is the wrong trade.
        log.debug("could not read endpoints for %s: %s", config.model, error)
        return ()
    return endpoint_policy.rank([endpoint_policy.from_api(row) for row in rows if row], by)


def forget() -> None:
    """Drop the cache. For tests, and for the settings UI after a model change."""
    with _lock:
        _cache.clear()
