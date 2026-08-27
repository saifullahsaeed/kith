"""Telling a new task apart from one already on the board.

`tools/tasks.py` asks this before it files anything: does this goal read like a goal he has
already written down? He rephrases himself every time, so it is word overlap rather than string
comparison.

**This file used to be about loop detection and most of it has gone with the feature.** The
module carried a second signal — the *shape* of a round, the handful of tools reached for — and
the tests below pinned it against real tick data from a loop that defeated the prose signal. That
machinery ran under the self-directed tick loop. Nothing runs unattended now, `same_shape`,
`advanced`, `ADVANCE_TOOLS` and `STALL_BREAK` had no caller anywhere in the tree, and two of the
thresholds behind them were live settings on the Advanced screen: you could type a number into
"How alike two rounds must be" and nothing at all would happen.

The loop fixtures are kept, as one test, and inverted. They used to prove that shape matching was
necessary; they now record what the surviving half cannot do, so that the next person to reach
for prose overlap as a stuck-detector finds the measurement rather than repeating it.
"""

from __future__ import annotations

from kith.domain.stall import signature, similar

# Verbatim from two consecutive real ticks. Same act, different words: he re-derived the same DOM
# selector both times and wrote nothing down.
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


class TestTwoWordingsOfOneJob:
    def test_the_same_task_asked_twice_is_caught(self):
        """The case this exists for: two goals filed minutes apart, reworded."""
        first = signature("Write a scraper for the CMA circulars page")
        again = signature("Write a scraper for the CMA circulars page and save the results")
        assert similar(first, again)

    def test_a_genuinely_different_task_is_not(self):
        """Too loose and he refuses to file something new, which is the worse failure — a task
        he declines to write down is a task nobody remembers."""
        assert not similar(signature(LOOP_A), signature(PROGRESS))

    def test_an_empty_side_matches_nothing(self):
        """A task with no describable goal is not a duplicate of everything."""
        assert not similar(frozenset(), signature(PROGRESS))
        assert not similar(signature(PROGRESS), frozenset())

    def test_short_words_do_not_carry_the_match(self):
        """Fingerprints are built from words of four letters or more, so two sentences made of
        the same articles and prepositions are not the same task."""
        assert not similar(signature("it is the one and the same"), signature("and the one is it"))


class TestWhatWordOverlapCannotSee:
    def test_a_rediscovery_loop_scores_far_below_the_bar(self):
        """Kept from the loop-detection tests, and it is the reason they existed.

        Two consecutive real ticks that rediscovered the identical fact and wrote nothing down.
        Word overlap scores them at about 0.21 against a 0.6 bar — he rephrases himself every
        time, so prose cannot see a loop. That is a limitation of what remains, not a bug in it:
        the signal that could see it was tool shape, and it has been deleted along with the
        unattended loop it served. Anyone reaching for this function as a stuck-detector should
        read this number first.
        """
        a, b = signature(LOOP_A), signature(LOOP_B)
        overlap = len(a & b) / len(a | b)
        assert overlap < 0.3, f"the measurement this test records has changed: {overlap:.3f}"
        assert not similar(a, b)


class TestASettingThatDoesNothingSaysSo:
    """The reason two dead keys survived in a real settings file for months.

    `min_gap` and `task_tick_cap` were left behind when the tick loop was removed. Nothing read
    them, and the Advanced screen could not show them either — it is drawn from the declared
    knobs, so a key that is not one is invisible there. The only way to find them was to read the
    file and then grep the tree for each key by hand.
    """

    def test_a_key_nothing_reads_is_dropped_and_reported(self, tmp_path, capsys):
        import json

        from kith.services import tuning

        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"max_rounds": 12, "task_tick_cap": 50, "min_gap": 5.0}))
        tuning.use_file(settings)
        try:
            assert tuning.value("max_rounds") == 12
            said = capsys.readouterr().out
            assert "task_tick_cap" in said and "min_gap" in said
            assert "nothing reads" in said
        finally:
            tuning.use_file(None)

    def test_a_declared_key_is_left_alone(self, tmp_path, capsys):
        import json

        from kith.services import tuning

        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"max_rounds": 12}))
        tuning.use_file(settings)
        try:
            assert tuning.value("max_rounds") == 12
            assert "nothing reads" not in capsys.readouterr().out
        finally:
            tuning.use_file(None)
