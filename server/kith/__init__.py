"""Kith — a small, independent Flask service.

It talks to a local Ollama, owns the persona and chat parameters, splits reasoning from the
answer, and streams both. The web UI is just one client; the API is documented with OpenAPI
(Swagger UI at /docs) so anything can drive it.

**This file holds no imports, and that is the point.**

It used to build the application. `create_app`, the CORS policy, the background startup and
the logging all lived here, above a header that imported APIFlask, every route module,
`config`, the migrations, embeddings, the scheduler, the connection manager and the MCP
manager. That is the composition root, and a composition root is entitled to reach into every
layer — but Python runs a package's `__init__` before any module inside it, so *every* import
of *anything* under `kith.` ran the whole of it.

Measured, with the venv interpreter rather than by reading the graph::

    import kith.domain.stall     ->  153 kith modules, 905 modules total, 0.68s
                                     apiflask, flask, requests, sqlite3, yaml

`domain/stall.py` is 90 lines of regex and set arithmetic whose own docstring says it needs no
database, no clock and no IO. Importing it started a web framework.

This is upstream of the layering work rather than part of it. A `kernel/` package that imports
nothing is clean in the import graph and still pays for Flask at runtime, because reaching it
means running this file first — so the extraction would have looked like it worked while
changing nothing about what a module actually costs to load. It also quietly set the floor for
every test in the suite, none of which can pay less than 0.68s and 905 modules to import
anything at all.

`create_app` now lives in `kith.app`, which is a module like any other and is loaded only by
whoever asks for it. The accessor below keeps `from kith import create_app` working — that is
what `server/app.py` and two tests say, and there is no reason to make them say something else
to buy this. It resolves on attribute access, so the cost lands on the caller that wants an
application and on nobody else.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = ["create_app"]

if TYPE_CHECKING:
    # For the type checker and for anything reading this file to find out where `create_app`
    # went. Never executed, so it costs nothing at runtime — which is the entire exercise.
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
