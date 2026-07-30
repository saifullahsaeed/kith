"""Letting Kith run on his own.

* ``runner`` — the loop, the tick, and mode selection.
* ``directives`` — what each kind of tick asks of him, as markdown.

Loop detection lives in ``kith.domain.stall``: it is pure logic, and keeping it
testable in isolation is the only reason we ever found out it was broken.
"""

from kith.autonomy.runner import AutonomyRunner, runner

__all__ = ["AutonomyRunner", "runner"]
