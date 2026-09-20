"""The present-state block gets a total, and its sources compete for it.

Before this there were seven caps and no arbiter. `project_context.LIMITS` capped tasks at
{8, 10, 6}, `memory_context` capped core at 30 and recent at 8, the channel at 8, the project
description at 600 characters, milestones at 3 — every one of them defensible alone, and
nothing anywhere that knew the total. Measured on a real database, a brand-new conversation
about nothing carried 13,362 characters before the person typed a word, of which roughly
10,500 was another project's memories.

That is not a cap that was set too high. It is the absence of anyone asking "and how much is
all of this together", which is a question a per-source constant structurally cannot answer.

So: one budget, and the sources bid. The allocator is pure — it takes blocks and a number and
returns the blocks that fit — because the thing that decides what to drop must be testable
without a database, a clock, or a model.
"""

from __future__ import annotations

from kith.domain import present


def block(key: str, text: str, tier: int = present.WORTH_HAVING, score: float = 0.0, trimmable=False):
    return present.Block(key=key, text=text, tier=tier, score=score, trimmable=trimmable)


class TestWhatAlwaysSurvives:
    def test_an_essential_block_is_paid_even_when_the_budget_is_gone(self):
        """The clock is the case. A turn that does not know the time is wrong in a way that
        being over budget is not, and the block is 86 characters — the budget was never the
        reason to drop it."""
        kept = present.allocate([block("clock", "x" * 500, tier=present.ESSENTIAL)], budget=10)
        assert [b.key for b in kept] == ["clock"]

    def test_essentials_are_paid_before_anything_else_regardless_of_order(self):
        blocks = [
            block("memories", "m" * 100),
            block("clock", "c" * 100, tier=present.ESSENTIAL),
        ]
        kept = present.allocate(blocks, budget=150)
        assert [b.key for b in kept] == ["clock"]


class TestHowTheRestCompete:
    def test_a_higher_tier_wins_the_room_over_a_lower_one(self):
        blocks = [
            block("nice", "n" * 100, tier=present.NICE_TO_HAVE),
            block("worth", "w" * 100, tier=present.WORTH_HAVING),
        ]
        kept = present.allocate(blocks, budget=100)
        assert [b.key for b in kept] == ["worth"]

    def test_within_a_tier_the_more_relevant_block_wins(self):
        """This is the whole answer to the isolation complaint. Two memories, one budget: the
        one that has something to do with the conversation takes the slot, and the other stays
        in `recall` where the tool description always said it was."""
        blocks = [
            block("stale", "s" * 100, score=0.1),
            block("apt", "a" * 100, score=0.9),
        ]
        kept = present.allocate(blocks, budget=100)
        assert [b.key for b in kept] == ["apt"]

    def test_a_block_that_does_not_fit_is_skipped_not_cut_in_half(self):
        """A paragraph sliced mid-sentence reads as corruption and costs the same as one that
        was left out. Only blocks that say they are a list may be shortened."""
        kept = present.allocate([block("prose", "p" * 100)], budget=60)
        assert kept == []

    def test_a_smaller_block_behind_it_still_gets_its_turn(self):
        """Skipping is not stopping. The budget has room left and something fits in it."""
        blocks = [block("big", "b" * 100, score=0.9), block("small", "s" * 40, score=0.5)]
        kept = present.allocate(blocks, budget=60)
        assert [b.key for b in kept] == ["small"]


class TestTrimmingAList:
    def test_a_list_gives_up_its_last_lines_to_fit(self):
        text = "[Your projects]\n- one\n- two\n- three"  # 35 chars; the first two lines are 27
        kept = present.allocate([block("projects", text, trimmable=True)], budget=27)
        assert kept[0].text == "[Your projects]\n- one\n- two"

    def test_a_list_trimmed_to_its_heading_alone_is_dropped(self):
        """A heading with nothing under it is a promise the block does not keep."""
        text = "[Your projects]\n- one\n- two"
        kept = present.allocate([block("projects", text, trimmable=True)], budget=16)
        assert kept == []


class TestTheEdges:
    def test_an_empty_block_never_takes_a_slot(self):
        kept = present.allocate([block("nothing", ""), block("something", "s" * 10)], budget=100)
        assert [b.key for b in kept] == ["something"]

    def test_the_order_out_is_the_order_the_prompt_wants_not_the_order_they_won_in(self):
        """Allocation ranks by tier and relevance; the prompt is read top to bottom by a model
        and its order is a separate decision the caller already made. Scrambling it to match
        the auction would put the clock under the task list."""
        blocks = [
            block("first", "f" * 10, score=0.1),
            block("second", "s" * 10, score=0.9),
        ]
        kept = present.allocate(blocks, budget=100)
        assert [b.key for b in kept] == ["first", "second"]
