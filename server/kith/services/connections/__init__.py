"""Where Kith thinks: choosing it, checking it, and keeping it.

* ``manager`` — the lifecycle. Load what is stored, try a candidate without saving,
  commit a validated choice.
* ``providers`` — one class per way of reaching a model, behind a shared interface.
* ``readiness`` — what else is available, and what its absence costs.

The domain type itself is ``kith.domain.connection.Connection``: pure, immutable, and
the single home for questions like "does this provider support web search" that three
different modules used to answer separately.
"""

from __future__ import annotations

from kith.services.connections import providers, readiness
from kith.services.connections.manager import ConnectionManager, ProbeResult
from kith.services.search_setup import SearchManager
from kith.settings import CONFIG_DB_PATH

#: The app has one configuration database, so one manager over it. Constructed here
#: rather than per-request: it holds no mutable state, only the path it writes to.
manager = ConnectionManager(config_db=CONFIG_DB_PATH)

#: Search is a second, smaller decision over the same database. Separate object
#: because it answers a different question, constructed here for the same reason.
search = SearchManager(config_db=CONFIG_DB_PATH)

__all__ = [
    "ConnectionManager",
    "ProbeResult",
    "SearchManager",
    "manager",
    "providers",
    "readiness",
    "search",
    "snapshot",
]


def snapshot() -> dict:
    """Everything the onboarding UI needs, in one request."""
    connection = manager.current()
    chosen = search.current(connection)
    return {
        "onboarded": manager.is_onboarded(),
        "connection": connection.public(),
        "providers": providers.cards(),
        "search": {
            "current": chosen.public(),
            "options": search.options(connection),
        },
        "checks": [check.public() for check in readiness.report(connection, chosen)],
    }
