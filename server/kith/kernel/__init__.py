"""The runtime primitives, and the one package that imports nothing.

Every other package may reach for this one, which is only safe because it needs none of
them. `tests/test_the_layers_point_one_way.py::test_the_kernel_imports_nothing_from_kith`
asserts exactly that, including sideways — a `kith.` import of any kind from in here is a
mistake, because the moment one exists the packages beneath start needing function-local
imports again and the whole exercise unwinds.

**The membership rule is about storage, not purity.** "Pure" is the wrong test: it would
admit half of `domain/` and exclude `changes`, which holds live subscriber queues. The rule
that matches what is actually here is:

    A module belongs in the kernel when every layer needs it and nothing can supply it,
    because there is nothing behind it. If it needs a path, a database or a setting, it
    is not kernel — it belongs at the layer that owns that storage.

Which is why `session_context` is here and the three functions that resolve a project
through the repositories are not; why the clock's `now_iso` is here and `local_tz` — which
reads a stored timezone — is not.

**This package owns process-global mutable state, deliberately.** `changes.stream` is a live
set of subscriber queues; `live_turns` holds the running turn's output under a lock and the
readers following it; `session_context` holds four `ContextVar`s. That is not an accident of
extraction, it is what "ambient" means: these are the things a turn is *in*, and they cannot
be passed down because the code that reads them is fifty frames below the code that sets them.

Two consequences worth knowing before adding anything here:

* **State in a kernel is global by construction.** A second process gets its own copy and a
  test gets whatever the last test left, so anything stateful needs its own reset, and the
  `ContextVar`s need to stay `ContextVar`s — per-thread by construction is the property that
  lets two chats stream at once.
* **A `ContextVar` is identified by object, not by name.** Two built from the same string are
  different variables, and reading the wrong one returns the default rather than raising —
  which for `session_context` is `""`, its documented legitimate answer for "nothing claims
  this work". A half-finished move of this module is therefore silent. That is why it was
  moved in one commit with no re-export left behind.
"""

from __future__ import annotations
