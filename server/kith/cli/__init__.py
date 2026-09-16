"""The command line.

Kith had exactly two front doors — the Electron window and an HTTP API guarded by a token in
a 0600 file — and neither is reachable from a shell. That asymmetry had a cost that showed up
the moment two agents worked on one repository: *Kith* can drive Claude, because Kith has
``run_command`` and can type ``claude -p``; Claude could not drive Kith, because there was
nothing to type. Not a missing capability. A missing client.

So this is a client and nothing more. Every command here maps onto an endpoint that already
existed; no route was added, no service was touched. The rule that keeps it that way is in
``tests/test_the_cli_is_only_a_client.py``: this package may import ``kith.settings`` and
nothing else from ``kith``. Reaching into ``services`` would give the CLI a second, subtly
different way of doing what the server does — two implementations of "start a conversation",
one of which nobody tests against the other.

Three consequences of being only a client, each of which removes work rather than adding it:

* **No local transcript cache.** ``/api/chat`` rebuilds a resumed conversation's history from
  disk and ignores whatever the client sent, so a cache here could only ever be wrong in a way
  nothing would notice.
* **No writes to his mind.** Memory, tasks, notes, the journal — the CLI reads them and cannot
  edit them. Settings are the exception, because those are yours rather than his.
* **Standard library only.** No ``httpx``, no ``click``. Partly so the frozen binary is a few
  megabytes rather than forty, mostly because a CLI another agent shells out to per message
  pays its import cost on every single call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = ["main"]

if TYPE_CHECKING:
    # For the type checker, and so a reader can see where `main` went. Never executed, so the
    # lazy resolution below is still what happens at runtime — the same arrangement, and for
    # the same reason, as `kith/__init__.py`.
    from kith.cli.main import main


def __getattr__(name: str):
    """Resolve `main` on demand, for the same reason `kith/__init__` resolves `create_app`."""
    if name == "main":
        from kith.cli.main import main

        return main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
