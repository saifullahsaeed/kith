"""Loop detection, pinned against the tick history that defeated it.

This existed and did nothing for weeks — twice over. It was gated on a journal Kith
never wrote, and even once fed, its prose fingerprint scored 0.212 on two ticks that
rediscovered the identical fact, because he rephrases himself every time.

The fixtures below are real tick data from that loop and from the ticks either side
of it, so a future change to the thresholds has to survive the case that mattered
rather than a case someone invented.
"""

from __future__ import annotations

from kith.domain.stall import ADVANCE_TOOLS, STALL_BREAK, same_shape, signature, similar

# Verbatim from two consecutive real ticks. Same act, different words: he re-derived
# the same DOM selector both times and wrote nothing down.
LOOP_A = (
    "The project directory exists but is empty of code. Let me first check what tools I "
    "have available, then explore the actual HTML structure of a CMA circulars page so I "
    "can write the scraper accurately"
)
LOOP_B = (
    "Good — working file exists with the full plan, but no code yet. Let me verify the "
    "actual page structure before writing the scraper. Let me look at the actual HTML to "
    "find the link structure"
)
PROGRESS = (
    "Wrote the scraper module and ran it against the saved page — extracted 24 circulars with dates and refs"
)

# The tool shapes of those same ticks.
SHAPE_LOOP_A = frozenset({"read_file", "list_files", "shell", "browse_page", "fetch_url"})
SHAPE_LOOP_B = frozenset({"read_file", "shell", "browse_page", "fetch_url"})
SHAPE_BUILDING = frozenset({"read_file", "write_file", "shell", "check_item"})


def test_prose_alone_cannot_see_the_loop() -> None:
    """Documents WHY shape matching exists — don't 'simplify' it away.

    These two ticks are the same act. Word overlap scores ~0.21 against a 0.6 bar,
    so a prose-only detector reports progress forever.
    """
    assert not similar(signature(LOOP_A), signature(LOOP_B))


def test_tool_shape_catches_the_loop() -> None:
    assert same_shape(SHAPE_LOOP_A, SHAPE_LOOP_B)


def test_tool_shape_does_not_punish_real_work() -> None:
    """Two productive ticks look alike too, so shape must not convict on its own."""
    assert not same_shape(SHAPE_LOOP_A, SHAPE_BUILDING)


def test_advancing_the_work_clears_the_suspicion() -> None:
    """The progress guard: identical shapes are fine when something moved forward."""
    assert SHAPE_BUILDING & ADVANCE_TOOLS
    assert not SHAPE_LOOP_A & ADVANCE_TOOLS
    assert not SHAPE_LOOP_B & ADVANCE_TOOLS


def test_narrating_the_loop_is_not_advancing() -> None:
    """He must not be able to escape detection by commenting instead of leaving."""
    assert "comment_on_task" not in ADVANCE_TOOLS
    assert "journal" not in ADVANCE_TOOLS


def test_writing_files_is_not_advancing_either() -> None:
    """A deliberate trade, worth pinning: during the real loop he DID write files —
    they were raw HTML dumps. Counting write_file as progress would have let the
    loop run forever."""
    assert "write_file" not in ADVANCE_TOOLS


def test_a_tiny_tick_is_not_fingerprinted() -> None:
    """One or two calls match by chance; too little signal to convict on."""
    assert not same_shape(frozenset({"read_file"}), frozenset({"read_file"}))


def test_the_real_sequence_breaks_out_on_the_third_tick() -> None:
    """Replay of the actual history: productive, then three loop ticks."""
    from collections import deque

    shapes: deque = deque(maxlen=6)
    stall = 0
    fired_at = None

    for index, shape in enumerate([SHAPE_BUILDING, SHAPE_LOOP_A, SHAPE_LOOP_B, SHAPE_LOOP_B]):
        advanced = bool(shape & ADVANCE_TOOLS)
        repeated = (not advanced) and any(same_shape(shape, seen) for seen in shapes)
        stall = stall + 1 if repeated else 0
        shapes.append(shape)
        if stall >= STALL_BREAK and fired_at is None:
            fired_at = index

    assert fired_at == 3, f"breakout fired at tick {fired_at}, expected the third loop tick"
