"""The parts of a turn, pulled out of the loop one at a time.

`services/agent_loop.py` was 1,406 lines with a 340-line generator at cyclomatic complexity 40
in the middle of it. Nothing was *wrong* with it — it is the most carefully reasoned file in
the codebase and every branch has its measurement written beside it — but it is where every
future change lands, and one function that does eight things is eight reasons to open it.

Moved here in order of how little they share with the rest. A module in this package must be
readable without the loop open beside it; anything that still needs the round's local state
stays in `agent_loop` until it can be given what it needs instead.

**These are moves, not rewrites.** The comments came with the code because they are the
specification — they record what a decision cost, and several of them cost real money to
learn. A behaviour change smuggled in under a refactor is the one outcome worse than the long
function, so `tests/test_the_loop_keeps_its_shape.py` pins the two invariants that fail
silently, and every move is checked against it.
"""

from __future__ import annotations
