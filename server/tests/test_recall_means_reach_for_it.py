"""`recall` memories stop arriving unasked.

There are exactly two memory levels — `MEMORY_LEVELS = ("core", "recall")` — and
`set_memory_level`'s own description tells the model what they mean: core is "always present",
recall is "kept, retrieved when you reach for it".

`recent_memories` then selected `level != "core"`, which with two levels is precisely "every
recall memory", and injected the eight most recent of them into every turn. So the only
non-core level was the one being auto-injected, and the contract the tool advertises was broken
by construction rather than by accident. Measured on the real database: a conversation about
nothing opened carrying a missile simulator's physics notes, a Flutter toolchain listing and a
domain-registry finding, none of which had anything to do with anything.

The fix is not a smaller limit. A recall memory now has to *earn* its slot by having something
to do with the conversation, which is the same rule every other source in the present-state
block now plays by.
"""

from __future__ import annotations

from kith.domain import present


class TestScoringAMemoryAgainstTheConversation:
    def test_a_memory_about_the_subject_scores_high(self):
        score = present.relevance(
            "the odoo connector is slow when it syncs invoices",
            "The Odoo connector feels slow because invoice sync is sequential.",
        )
        assert score > 0.5

    def test_a_memory_about_something_else_scores_zero(self):
        """This is the one that matters. A chat about CSS must not carry missile physics."""
        score = present.relevance(
            "why is this button the wrong colour in dark mode",
            "The missile lands 38 km east of target and that is correct physics.",
        )
        assert score == 0.0

    def test_common_words_alone_are_not_a_match(self):
        """Without a stoplist, every memory matches every conversation on 'the' and 'that',
        which is the same as having no scoring at all."""
        score = present.relevance(
            "what is the thing that we should do about this",
            "The thing that should be done about this is the other thing.",
        )
        assert score == 0.0

    def test_an_empty_conversation_scores_nothing_rather_than_everything(self):
        """The first turn of a new chat has no history to match against. Scoring that as a
        perfect match would inject everything into exactly the conversation this work exists
        to clean up."""
        assert present.relevance("", "Anything at all about anything.") == 0.0

    def test_a_memory_with_no_distinctive_words_cannot_score(self):
        assert present.relevance("odoo invoices", "the and that with this") == 0.0

    def test_the_score_never_leaves_the_unit_interval(self):
        score = present.relevance(
            "odoo odoo odoo invoices invoices connector",
            "odoo invoices connector",
        )
        assert 0.0 <= score <= 1.0


class TestWhichMemoriesAreEligibleAtAll:
    def test_core_is_always_eligible(self):
        kept = present.eligible_memories(
            [{"id": 1, "level": "core", "content": "Nothing to do with the chat."}],
            about="something else entirely",
        )
        assert [m["id"] for m in kept] == [1]

    def test_a_recall_memory_with_nothing_to_do_with_it_is_left_behind(self):
        kept = present.eligible_memories(
            [{"id": 1, "level": "recall", "content": "Missile apogee is 180 kilometres."}],
            about="why is the button the wrong colour",
        )
        assert kept == []

    def test_a_recall_memory_about_the_subject_comes_along(self):
        kept = present.eligible_memories(
            [{"id": 1, "level": "recall", "content": "The button colour comes from --accent."}],
            about="why is the button the wrong colour",
        )
        assert [m["id"] for m in kept] == [1]

    def test_the_most_relevant_recall_memory_comes_first(self):
        memories = [
            {"id": 1, "level": "recall", "content": "Buttons exist somewhere in the codebase."},
            {"id": 2, "level": "recall", "content": "Button colour comes from the accent token."},
        ]
        kept = present.eligible_memories(memories, about="button colour accent token")
        assert kept[0]["id"] == 2

    def test_core_leads_whatever_the_scores_say(self):
        """Core is 'the front of your mind'. It reads wrong underneath something he had to be
        reminded of, and the block says so in words."""
        memories = [
            {"id": 1, "level": "recall", "content": "Button colour comes from the accent token."},
            {"id": 2, "level": "core", "content": "Something unrelated but always true."},
        ]
        kept = present.eligible_memories(memories, about="button colour accent token")
        assert [m["id"] for m in kept] == [2, 1]


class TestALongMemoryCanStillBeRelevant:
    """Found by measuring against the real database rather than by reasoning about it.

    Scoring "how much of this memory is already on the table" reads sensibly and is wrong in
    exactly one direction, which happens to be the direction the real data lies in. Kith's
    memories are long — the median global one is over 900 characters and several are past
    2,000 — and a question is a dozen words. Asking a 150-term memory to have a third of its
    vocabulary present in a 10-term question is asking for something arithmetically impossible,
    so *every* recall memory scored near zero and the level became unreachable rather than
    merely quieter.

    Measured before the fix: the single most on-subject question anyone could ask about the
    ballistic-sim memory scored **0.04** against a bar of 0.34.

    The fix is the overlap coefficient — intersection over the *smaller* vocabulary — which is
    the standard answer for comparing texts of wildly different lengths and is symmetric, so
    neither a long memory nor a long conversation is punished for its size.
    """

    LONG = (
        "Ballistic Defense Sim lives at ~/Kith/missile-sim, a git repository with everything "
        "committed. The simulator models boost phase, midcourse and terminal intercept against "
        "a rotating oblate earth, validates gravity against a finite-difference geopotential "
        "gradient, and carries twenty-nine validation checks covering atmospheric drag, "
        "coriolis deflection, staging separation, seeker acquisition geometry and the terminal "
        "guidance law. Two things are unfinished and stated in the README: the radar cross "
        "section model is a placeholder, and multi-stage interceptors share one body spec."
    )

    def test_a_short_question_on_the_subject_reaches_a_long_memory(self):
        score = present.relevance(
            "where does the ballistic missile sim repo live and what is committed", self.LONG
        )
        assert score >= present.RELEVANT_ENOUGH

    def test_and_a_question_about_something_else_still_does_not(self):
        score = present.relevance("a good name for a coffee shop", self.LONG)
        assert score < present.RELEVANT_ENOUGH

    def test_the_long_memory_is_admitted_only_on_subject(self):
        memories = [{"id": 1, "level": "recall", "content": self.LONG}]
        assert present.eligible_memories(memories, "the missile sim repo and its validation checks")
        assert present.eligible_memories(memories, "a good name for a coffee shop") == []
