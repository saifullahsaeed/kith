"""Telling a new task apart from one he already has.

Pure functions and a threshold — no database, no clock, no IO. That matters here more than
elsewhere: it decides whether something he is about to file is a thing he has already filed, it
has been wrong twice in ways nobody spotted for weeks, and it is only trustworthy if it can be
tested directly against real history. Living in ``domain`` is what makes that possible.

One question, asked by `tools/tasks.py` before it files anything: does this goal read like a goal
already on the board? He rephrases himself every time, so this is word overlap rather than string
comparison.

This module was once also about loop detection, for the self-directed tick loop. Nothing runs
unattended any more, so the second signal it carried — the *shape* of a round, scored against a
higher bar than prose — had no caller left, and two of its thresholds were live settings on the
Advanced screen that could be typed into and changed nothing. Deleted rather than kept warm,
because a reader cannot tell an unused constant from a load-bearing one without grepping the
tree. Word overlap alone was never enough to catch a loop; it is enough for two task
descriptions written minutes apart, which is all it is asked for now.
"""

from __future__ import annotations

import re

# Word-overlap threshold for a task goal against one already on the board.
PROSE_MATCH = 0.6

#: Shorter words than this are too common to carry meaning in a fingerprint.
_MIN_WORD = 4


def signature(text: str) -> frozenset[str]:
    """A rough fingerprint of a journal entry — its meaningful words."""
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return frozenset(word for word in words if len(word) >= _MIN_WORD)


def similar(a: frozenset[str], b: frozenset[str], *, threshold: float = PROSE_MATCH) -> bool:
    """Do two prose fingerprints overlap enough to call them the same step?

    The threshold arrives as an argument. It used to be fetched — `_threshold("prose_match")`,
    with a function-body import of `kith.services.tuning` under a docstring explaining that the
    import was deferred "to keep this module free of service imports". It was not free of them;
    it was free of them *at import time*, which is a different and much weaker property, and the
    file's own header claims "no database, no clock, no IO" while that call opened `config.db`.

    The default is the declared default of the `prose_match` tunable, so nothing changes for a
    caller that has not tuned it, and `tools/tasks.py` — an adapter, at the layer where reading
    a setting is allowed — passes the live value so nothing changes for one that has.
    """
    if not a or not b:
        return False
    union = len(a | b)
    return union > 0 and len(a & b) / union >= threshold
