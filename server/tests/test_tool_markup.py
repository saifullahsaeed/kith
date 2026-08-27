"""Scrubbing tool calls a model wrote as prose.

Every case is checked three ways — whole text, one character at a time, and in
seven-character chunks — because the streaming path is where this went wrong, and it
went wrong in a way that only appears at particular chunk boundaries.
"""

from __future__ import annotations

import pytest

from kith.domain.tool_markup import ToolMarkupFilter, strip_tool_markup

#: Verbatim from a real turn, which is why the closer is orphaned and the tags are
#: spaced out: the model was narrating, not calling.
LEAKED = (
    "Added it as task #33. I'll get to it this morning.\n\n"
    "Anything specific to prioritize? <function=web_search> <parameter=query> "
    "Pakistani schools in Riyadh FBISE curriculum affordable </parameter> "
    "<parameter=max_results> 10 </parameter> </function> </tool_call>"
)


def streamed(text: str, size: int) -> str:
    """The filter's output when text arrives in `size`-character pieces."""
    filter_ = ToolMarkupFilter()
    chunks = [text[i : i + size] for i in range(0, len(text), size)]
    return ("".join(filter_.feed(chunk) for chunk in chunks) + filter_.flush()).strip()


def scrubbed_every_way(text: str) -> str:
    """The result, asserting the three paths agree before returning it."""
    whole = strip_tool_markup(text)
    # Compared on words: a removed block leaves whitespace whose exact shape differs
    # between paths and doesn't matter to a reader.
    assert streamed(text, 1).split() == whole.split(), "char-by-char disagreed"
    assert streamed(text, 7).split() == whole.split(), "7-char chunks disagreed"
    assert streamed(text, 1000).split() == whole.split(), "one big chunk disagreed"
    return whole


class TestRemoval:
    def test_the_real_leaked_turn(self):
        result = scrubbed_every_way(LEAKED)
        assert (
            result == "Added it as task #33. I'll get to it this morning.\n\nAnything specific to prioritize?"
        )
        # The query text is the part that used to survive streaming, reading as if he
        # had written it.
        assert "Pakistani schools" not in result
        assert "web_search" not in result

    def test_the_uppercase_shape(self):
        text = 'I will look.\n<FUNCTION>web_search(query="x", max_results=10)</FUNCTION>\nThen report.'
        assert scrubbed_every_way(text) == "I will look.\n\nThen report."

    def test_several_blocks_in_one_answer(self):
        assert scrubbed_every_way("A <FUNCTION>f(1)</FUNCTION> B <FUNCTION>f(2)</FUNCTION> C").split() == [
            "A",
            "B",
            "C",
        ]

    def test_an_unclosed_block_takes_the_rest_with_it(self):
        # He started a call and never finished it, so nothing after it is prose either.
        assert scrubbed_every_way("Here's my answer. <function=web_search> <parameter=query> never") == (
            "Here's my answer."
        )

    def test_a_stray_closer_goes_quietly(self):
        assert scrubbed_every_way("A real answer. </tool_call>") == "A real answer."


class TestRestraint:
    """He writes code and prose about code. Over-eager filtering is the worse failure."""

    @pytest.mark.parametrize(
        "text",
        [
            "Use `if (a < b) { return x > y; }` and the <div> tag. Compare a<b and c>d.",
            "The plan: research -> shortlist -> compare. 3 < 5 always.",
            "In TypeScript: const f = <T,>(x: T) => x;",
            "Generic HTML like <span class='x'>hi</span> is untouched.",
            "Just a normal answer about schools in Riyadh.",
            "A function= sign with no tag around it.",
        ],
    )
    def test_ordinary_text_survives_intact(self, text):
        assert scrubbed_every_way(text) == text.strip()


class TestStreamingMechanics:
    def test_a_tag_split_across_two_deltas_is_still_caught(self):
        filter_ = ToolMarkupFilter()
        # The exact split that broke the first implementation: the opener completed in
        # one delta, so an incremental regex removed it as a stray and then let the
        # call's arguments through as prose.
        first = filter_.feed("Answer. <FUNC")
        second = filter_.feed('TION>web_search(query="x")</FUNCTION> Done.')
        whole = first + second + filter_.flush()
        assert "web_search" not in whole
        assert whole.split() == ["Answer.", "Done."]

    def test_a_partial_tag_is_held_not_emitted(self):
        filter_ = ToolMarkupFilter()
        assert filter_.feed("Answer. <FUNC") == "Answer. "
        # Half a tag reads as gibberish, so it must not reach anyone before it resolves.
        assert "<FUNC" not in "Answer. "

    def test_held_text_is_released_when_it_turns_out_to_be_prose(self):
        """A '<' is held only until it's clear it isn't a tag, then let through."""
        filter_ = ToolMarkupFilter()
        first = filter_.feed("Compare a <")
        assert first == "Compare a "  # the '<' is held, in case it opens a wrapper
        rest = filter_.feed("b for size.") + filter_.flush()
        # ...and released with the character that settled it, losing nothing.
        assert first + rest == "Compare a <b for size."

    def test_a_filter_still_works_after_a_flush(self):
        """`flush` cleared the held buffer but left `_suppressing` set, so a filter that ended
        mid-block swallowed every later chunk — having already been told the stream was over."""
        one = ToolMarkupFilter()
        one.feed("hello <FUNCTION>web_search(")
        assert one.flush() == ""
        assert one.feed("a fresh answer") == "a fresh answer"

    def test_flush_is_idempotent(self):
        filter_ = ToolMarkupFilter()
        filter_.feed("Done.")
        assert filter_.flush() == ""
        assert filter_.flush() == ""
