"""A brief is six sections, not a paragraph — and the room to write them.

The instruction this replaces named six things to preserve inside one blob and then said "be
concise" three different ways. A real fold of six turns came back as four lines about CI
sharding: every word true, and it had lost every question that had been asked, including ones
never answered. See `test_a_fold_keeps_what_you_asked` for the mechanical half of that fix —
the person's own messages are now carried past a fold word for word, which does not depend on
the model cooperating. This is the other half: the brief itself.

Two things were wrong and neither was the model.

**It asked for a paragraph.** Six concerns listed in one sentence get one sentence back that
gestures at all six. A heading per concern makes the model go and look for each separately.

**It never mentioned the person.** Decisions, facts, files, threads, current work — every item
was about the work, none about who asked for it. On a transcript that is 0.3% their words, an
instruction that does not name them produces a summary without them in it.

And a third thing that would have quietly beaten any prompt: `num_predict` was pinned at 1,600
tokens. Six sections do not fit in 1,600, so the cap decided the shape no matter what the
instruction said.
"""

from __future__ import annotations

from dataclasses import dataclass

from kith.services import history


class TestTheInstructionAsksForAShape:
    def test_every_section_is_named_in_the_instruction(self):
        for heading in history.SECTIONS:
            assert f"## {heading}" in history._INSTRUCTION, heading

    def test_the_sections_are_asked_for_in_order(self):
        found = [history._INSTRUCTION.index(f"## {h}") for h in history.SECTIONS]
        assert found == sorted(found)

    def test_the_person_is_one_of_them(self):
        """The section whose absence caused the whole thing. It is first because what was
        wanted is what everything else in the brief is in service of."""
        assert history.SECTIONS[0] == "What they asked for"
        assert "What the person wanted" in history._INSTRUCTION

    def test_that_section_asks_for_intent_and_not_a_replay(self):
        """Found by running it. The first draft asked for every request "quoted in their own
        words" and got exactly that on a real 146,000-character conversation — fifty verbatim
        lines, down to "keepgoimh", then the output cap arrived in the middle of section two
        and five of six sections were never written. `_what_they_asked` already carries those
        words; the brief paying for them again starves the parts only it can say."""
        assert "NOT a replay of their messages" in history._INSTRUCTION
        assert "carried forward separately" in history._INSTRUCTION

    def test_it_asks_for_quotes_rather_than_paraphrase(self):
        """The old one only said what *not* to do — "do not add, guess, or infer". A negative
        constraint prevents invention; it does not produce fidelity."""
        assert "Quote rather than paraphrase" in history._INSTRUCTION

    def test_it_still_forbids_inventing(self):
        """The one thing worth keeping from the old instruction."""
        assert "Invent nothing" in history._INSTRUCTION

    def test_it_says_what_to_do_with_its_own_previous_brief(self):
        """`compact` prefixes a refold with `[Summary so far]` — the model is handed its own
        last output and told nothing about it, so it compressed a summary. Do that on every
        fold of a long conversation and the beginning decays to nothing."""
        assert "[Summary so far]" in history._INSTRUCTION
        assert "Do not summarise it again" in history._INSTRUCTION

    def test_it_is_not_written_around_code(self):
        """Kith's conversations are spreadsheets, audits and research at least as often. A
        section called "files and code" would tilt every fold toward the one kind of work that
        names its artifacts in backticks."""
        lowered = history._INSTRUCTION.lower()
        for biased in ("code", "codebase", "function", "repository"):
            assert biased not in lowered, biased


@dataclass
class _Config:
    context_window: int = 0


class TestTheBriefHasRoomToBeThatShape:
    def test_a_big_window_gets_more_than_a_paragraph(self):
        """The cap was the real ceiling: 1,600 tokens is right for a paragraph and cannot hold
        six sections, so it decided the output regardless of the instruction."""
        assert history._summary_tokens(1_000_000) > 1_600

    def test_it_never_grows_into_another_transcript(self):
        """A fold earns its cost by being far smaller than what it replaces."""
        assert history._summary_tokens(10_000_000) == history._SUMMARY_MAX_TOKENS

    def test_a_small_local_model_is_left_exactly_as_it_was(self):
        """40K of window cannot spare more, and the floor is what this was for its whole life
        before the sections existed — so this change cannot regress that install."""
        assert history._summary_tokens(40_000) == history._SUMMARY_MIN_TOKENS

    def test_an_unknown_window_gets_the_floor(self):
        """Guessing a share of a number nobody recorded is worse in both directions — the same
        argument `_budget_chars` makes for falling back to a flat knob."""
        assert history._summary_tokens(0) == history._SUMMARY_MIN_TOKENS
        assert history._summary_tokens(-1) == history._SUMMARY_MIN_TOKENS

    def test_it_scales_with_the_window_rather_than_being_a_constant(self):
        """The thing the old value got wrong. This module already argues the case, about the
        input side: a fixed count is far too small next to a 1M cloud model and far too large
        next to a 40K local one."""
        assert history._summary_tokens(200_000) > history._summary_tokens(50_000)


class TestTheFoldStillWorks:
    """The shape changed; the policy did not."""

    def test_a_brief_still_replaces_the_middle_and_leads(self):
        convo: list[dict] = []
        for i in range(4):
            convo.append({"role": "user", "content": f"question {i}"})
            convo.append({"role": "assistant", "content": "answer " + "detail " * 300})
        kept, made = history.compact(
            convo, lambda _t: "## What they asked for\n- four questions", {}, max_chars=1, keep_recent=1
        )
        assert kept[0]["role"] == "system"
        assert history._SUMMARY_HEADER in str(kept[0]["content"])
        assert made is not None
        assert len(kept) < len(convo)

    def test_a_summariser_that_fails_still_leaves_the_history_alone(self):
        """Unchanged and load-bearing: a big prompt beats a broken turn."""
        convo = [{"role": "user", "content": "x" * 5_000}, {"role": "assistant", "content": "y" * 5_000}]
        kept, made = history.compact(convo, lambda _t: "", {}, max_chars=1, keep_recent=1)
        assert kept == convo and made is None
