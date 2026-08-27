"""Kith — a small, independent Flask service.

It talks to a local Ollama, owns the persona and chat parameters, splits reasoning from the
answer, and streams both. The web UI is just one client; the API is documented with OpenAPI
(Swagger UI at ``/docs``) so anything can drive it.

**This file holds no imports, and that is the point.** Python runs a package's ``__init__``
before any module inside it, so anything imported here is paid for by *every* import of
*anything* under ``kith.``. Building the application here meant that importing 90 lines of
regex cost a web framework — measured at 905 modules and 0.68s. ``create_app`` lives in
``kith.app`` instead, and the accessor below keeps ``from kith import create_app`` working so
the cost lands only on a caller that actually wants an application.

See ``docs/startup.md`` for the measurement and why a no-import ``kernel/`` package would not
have fixed it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = ["create_app"]

if TYPE_CHECKING:
    # For the type checker, and so a reader can see where `create_app` went. Never executed,
    # so it costs nothing at runtime — which is the entire exercise.
    from kith.app import create_app


def __getattr__(name: str) -> Any:
    """Resolve `kith.create_app` on demand (PEP 562).

    Only `create_app`. A general "forward anything to `kith.app`" would put the eager import
    back the first time someone touched an unrelated attribute, and would turn a typo into a
    Flask startup instead of an `AttributeError`.
    """
    if name == "create_app":
        from kith.app import create_app

        return create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
