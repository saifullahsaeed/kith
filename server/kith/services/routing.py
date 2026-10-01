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

**What a ranking is made on is the bills, where there are any.** The endpoint table says what
each host quotes; `services.host_costs` says what each one has actually charged this install,
and `domain.endpoints.priced` puts the two on one scale before anything is ranked. The table is
still what gets ranked — health, throughput and new hosts come from nowhere else — but the
price a host ranks on is its own record once it has one.
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
from kith.services import host_costs, tuning

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
_tables: dict[str, tuple[float, list[endpoint_policy.Endpoint]]] = {}
_lock = threading.Lock()


def for_conversation(config: Config, agent_db: Path, conversation_id: str) -> Routing:
    """Settings routing, plus the host order this conversation settled on when it began.

    The first turn of a conversation ranks and writes the answer down; every turn after it
    reads that row — until the model changes, because the row is a ranking of one model's hosts
    and the next model's are different slugs. A conversation that predates the column, or one started while the endpoint
    table could not be read, has nothing stored and ranks again next turn — which is the right
    behaviour and not a retry loop, because the write happens as soon as one succeeds.
    """
    base = _for_this_model(tuning.routing(), config)
    if not _rankable(base, config) or not conversation_id:
        return resolved(config) if not conversation_id else base

    from kith.services import conversations

    settled = conversations.provider_order(agent_db, conversation_id, config.model)
    if settled:
        return replace(base, order=settled)

    order = _fetch(config, base.prefer_by)
    if not order:
        return base
    conversations.remember_provider_order(agent_db, conversation_id, order, config.model)
    return replace(base, order=order)


def _for_this_model(base: Routing, config: Config) -> Routing:
    """The settings routing, without a pin that names no host of this model.

    The pin is one setting for every model, and a host belongs to one. Pinned to `openai/flex`
    for Sol, the same conversation switched to Gemini, Opus and Mimo and sent each of them
    `order: ["openai/flex"]` — which the router ignores for a model that host does not serve, and
    which also switched off this install's own host ranking, because a pin outranks it. So the
    other three models were routed unranked, by whoever OpenRouter picked (2026-10-01).

    Kept when the table cannot be read: not knowing is not evidence the pin is wrong, and
    honouring it is what happened before this existed.
    """
    if not base.pinned or not (config.api_key and is_openrouter(config.base_url)):
        return base
    hosts = _hosts(config)
    if not hosts or endpoint_policy.serves(hosts, base.pinned):
        return base
    return replace(base, pinned="")


def _hosts(config: Config) -> list[endpoint_policy.Endpoint]:
    """The model's endpoint table, from a short-lived cache: asked once per turn, read rarely."""
    key = f"hosts\n{config.model}"
    with _lock:
        cached = _tables.get(key)
        if cached and time.time() - cached[0] < _TTL_SECONDS:
            return cached[1]
    rows = _rows(config)
    with _lock:
        _tables[key] = (time.time(), rows)
    return rows


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
    base = _for_this_model(tuning.routing(), config)
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


def table(config: Config, by: str = "") -> list[endpoint_policy.Endpoint]:
    """Every host serving this model, in the order this install would prefer them.

    The same table `_fetch` ranks, handed out whole instead of reduced to slugs — because the
    one thing missing from pinning was ever being able to see what there was to pin *to*.
    The setting is a provider slug and no screen in Kith has ever shown one, so naming a host
    meant knowing it from somewhere else and spelling it right first time, with a wrong answer
    failing silently: `provider.order` with an unrecognised name is simply ignored.

    Unranked — `by` blank or unknown — is returned in OpenRouter's own order rather than ours,
    which is what a person who set `prefer_provider_by` to blank has asked to see. The rows are
    priced either way, so the figure the picker shows is the one a ranking would use.
    """
    rows = _priced(config)
    if by not in endpoint_policy.ORDERINGS:
        return rows
    order = {slug: place for place, slug in enumerate(endpoint_policy.rank(rows, by))}
    return sorted(rows, key=lambda one: order.get(one.slug, len(order)))


def _fetch(config: Config, by: str) -> tuple[str, ...]:
    return endpoint_policy.rank(_priced(config), by)


def _priced(config: Config) -> list[endpoint_policy.Endpoint]:
    """The endpoint table with each host priced on its bill, or on its calibrated quote.

    The bills are only read when there is a table to price: a model OpenRouter does not list has
    nothing to rank, and the first read of an empty record is the one that fills it.
    """
    rows = _rows(config)
    return endpoint_policy.priced(rows, host_costs.bills(config.model)) if rows else rows


def _rows(config: Config) -> list[endpoint_policy.Endpoint]:
    """The model's endpoint table off the network, or nothing at all.

    Total on purpose, for the reason the caller's docstring gives: not knowing costs the old
    `sort` behaviour, and raising here would cost the turn.
    """
    url = f"{config.base_url.rstrip('/')}/models/{config.model}/endpoints"
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            return []
        rows = ((response.json() or {}).get("data") or {}).get("endpoints") or []
    except (requests.exceptions.RequestException, ValueError) as error:
        # Logged rather than raised. The consequence of not knowing is "route the way we routed
        # last month", and taking a turn down over a ranking refinement is the wrong trade.
        log.debug("could not read endpoints for %s: %s", config.model, error)
        return []
    return [endpoint_policy.from_api(row) for row in rows if row]


def forget() -> None:
    """Drop the cache. For tests, and for the settings UI after a model change."""
    with _lock:
        _cache.clear()
        _tables.clear()
