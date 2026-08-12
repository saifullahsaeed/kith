"""Every HTTP route, one module per resource.

Importing this package registers them all: each module attaches its handlers to
the shared blueprint, so the imports below are the registration and only look
unused. Routes stay thin — validate, delegate, serialise. Anything that thinks
belongs in services/ or domain/.
"""

from __future__ import annotations

from kith.api.blueprint import api
from kith.api.routes import (  # noqa: F401 - imported for registration
    activity,
    brain,
    changes,
    chat,
    checkpoints,
    config,
    conversations,
    health,
    mcp,
    messages,
    permissions,
    persona,
    processes,
    renderer,
    roadmap,
    setup,
    skills,
    sources,
    tasks,
    tuning,
    workspace,
)

__all__ = ["api"]
