"""Subsystems that answer questions about a codebase, and run things in it.

An engine is a library, not a service. It is handed a path and some arguments, it computes,
and it returns a value. It does not read settings, resolve a project, decide whether work is
due, or run a turn — those are decisions, and decisions belong to `services/` above.

**The rank is the whole point.** `engine` sits below `services` and above `infra`, which the
layering test enforces on every run. That direction is what stops this package becoming the
next tangle: an analysis that wants a setting cannot reach for one, so it has to be handed the
value, and being handed a value is what keeps it a function rather than a dependency.

It arrived here almost free. Measured before the move, all 3,048 lines of `code/` and `lsp/`
reached into `services/` in exactly two lines — both function-local, both in `processes.py`,
and together a round trip: the scheduler called down for what had finished, and the callee
called back up into a private function of the scheduler. Reporting instead of waking removed
both, and nothing else needed touching.

Two packages, split on a line the old folder did not draw:

* ``code/`` — **reading**. Outline, repo map, and the language-server client next to them.
  Depends on nothing above `infra`, and `outline.py` imports nothing from `kith` at all.
* ``run/`` — **running**. Background processes and the test runner.

That split is not a taxonomy exercise. `services/code/`'s own package docstring described
outline, repomap and lsp and never mentioned `processes.py` or `testing.py` — 962 of its 1,709
lines, more than half of it, undocumented by the file whose job was to say what it contained.
Two subsystems were sharing a folder, and only one of them was being described.
"""

from __future__ import annotations
