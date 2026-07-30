"""Every HTTP route, one module per resource.

Importing this package registers them all: each module attaches its handlers to
the shared blueprint, so the imports below are the registration and only look
unused. Routes stay thin — validate, delegate, serialise. Anything that thinks
belongs in services/ or domain/.
"""

from __future__ import annotations

from kith.api.blueprint import api
from kith.api.routes import (  # noqa: F401 - imported for registration
    autonomy,
    brain,
    chat,
    config,
    health,
    messages,
    renderer,
    setup,
    sources,
    tasks,
    workspace,
)

__all__ = ["api"]
