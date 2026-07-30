"""Noticing that he is going in circles.

Pure functions and thresholds — no database, no clock, no IO. That matters here more
than elsewhere: this is the logic that decides whether Kith is stuck, it has been
wrong twice in ways nobody spotted for weeks, and it is only trustworthy if it can
be tested directly against real tick history. Living in ``domain`` is what makes
that possible.

Two signals, because prose alone does not work. He rephrases himself every time, so
two ticks that rediscover the identical fact score ~0.21 on word overlap against a
0.6 bar. What repeats is the *shape* of the tick — the same handful of tools reached
for the same way — which scores ~0.8. But shape alone would punish honest work, since
two solid rounds of writing-and-running code look alike too. So shape only counts
when the tick moved nothing forward.
"""

from __future__ import annotations

import re

# Near-identical steps in a row before he is forced to break out; a couple beyond
# that and the stuck thing is set aside for him.
STALL_BREAK = 2
STALL_GIVEUP = 4

# Word-overlap threshold for journal prose.
PROSE_MATCH = 0.6

# Tool sets are small and stable, so the bar is higher than for prose. Measured on
# real ticks: 0.80 on a genuine rediscovery loop, 0.13 on real progress.
SHAPE_MATCH = 0.7

# Below this a tick is too small to fingerprint — one or two calls match by chance.
SHAPE_MIN_TOOLS = 3

# Tools that mean the tick actually moved the work forward.
#
# Deliberately excludes comment_on_task and journal, or he could escape detection
# forever by narrating the loop instead of leaving it. Also excludes write_file:
# during the real loop he *did* write files — they were raw page dumps — so counting
# that as progress would have let it run indefinitely.
ADVANCE_TOOLS = frozenset({"check_item", "add_deliverable", "update_task", "update_milestone"})

# Words too common to carry meaning in a fingerprint.
_MIN_WORD = 4


def signature(text: str) -> frozenset[str]:
    """A rough fingerprint of a journal entry — its meaningful words."""
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return frozenset(word for word in words if len(word) >= _MIN_WORD)


def similar(a: frozenset[str], b: frozenset[str]) -> bool:
    """Do two prose fingerprints overlap enough to call them the same step?"""
    if not a or not b:
        return False
    union = len(a | b)
    return union > 0 and len(a & b) / union >= _threshold("prose_match")


def same_shape(a: frozenset[str], b: frozenset[str]) -> bool:
    """Did two ticks reach for the same kinds of tool?"""
    if len(a) < _threshold("shape_min_tools") or len(b) < _threshold("shape_min_tools"):
        return False
    union = len(a | b)
    return union > 0 and len(a & b) / union >= _threshold("shape_match")


def advanced(tools_used: frozenset[str]) -> bool:
    """Did this tick leave the work further along than it found it?"""
    return bool(tools_used & ADVANCE_TOOLS)


def _threshold(key: str) -> float:
    """A tuning value, imported lazily to keep this module free of service imports.

    This file is the pure loop-detection logic and is tested without a database; the
    import happens at call time so that stays true.
    """
    from kith.services import tuning

    return tuning.value(key)
