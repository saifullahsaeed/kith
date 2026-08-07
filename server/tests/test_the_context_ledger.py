"""What is in the window, by category — and the three things that read it.

`ContextBudget` answers "will the next round fit". Nothing answered "what is in there", so
every decision about what to reduce was made by recency alone. That is how the reducer reached
for the file he still needed while fifteen identical copies of it sat further up the same turn.

The ledger's numbers are load-bearing in three places — the threshold that triggers a fold, the
meter the person reads, and eviction — so the categories have to be *right*, not roughly right.
The first version of this got its biggest category silently wrong: it read `message["name"]` for
a tool result while the agent loop tags them with `tool_name`, so every file Kith had read
landed in "other tool results" and the code category read zero.
"""

from __future__ import annotations

import json

from kith.llm import ledger

PERSONA = "You are Kith. " * 200  # a realistic stable head


def _schema(name: str, size: int = 200) -> dict:
    return {
        "type": "function",
        "function": {"name": name, "description": "d" * size, "parameters": {}},
    }


def _turn() -> list[dict]:
    return [
        {"role": "system", "content": PERSONA + "\n\nIt is Tuesday. You feel curious."},
        {"role": "user", "content": "have a look at the settings page"},
        {
            "role": "assistant",
            "tool_calls": [{"id": "1", "function": {"name": "read_file", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_name": "read_file", "content": "x" * 4_000},
        {
            "role": "assistant",
            "tool_calls": [{"id": "2", "function": {"name": "read_skill", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_name": "read_skill", "content": "s" * 2_000},
        {
            "role": "assistant",
            "tool_calls": [{"id": "3", "function": {"name": "remember", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_name": "remember", "content": "ok"},
        {"role": "assistant", "content": "Here is what I found."},
    ]


class TestTheCategoriesAreRight:
    def test_a_file_he_read_is_counted_as_code_not_as_other(self):
        """The `tool_name` bug. A ledger that is quietly wrong about its biggest category is
        worse than no ledger, because it will be trusted."""
        book = ledger.take(_turn(), persona=PERSONA, window=1_000_000)
        assert book.of("code") > 0
        assert book.of("code") > book.of("tool_results")

    def test_a_skill_is_its_own_category(self):
        book = ledger.take(_turn(), persona=PERSONA, window=1_000_000)
        assert book.of("skills") > 0
        assert book.of("skills") < book.of("code")

    def test_the_persona_is_split_out_of_the_system_prompt(self):
        """ "System prompt: 6k" hides the useful fact, which is that most of it is cached across
        every turn Kith has ever had and is therefore nearly free."""
        book = ledger.take(_turn(), persona=PERSONA, window=1_000_000)
        assert book.of("persona") > book.of("system")
        assert book.of("system") > 0

    def test_the_persona_is_not_counted_twice(self):
        with_persona = ledger.take(_turn(), persona=PERSONA, window=1_000_000)
        without = ledger.take(_turn(), window=1_000_000)
        # Same total either way — naming the persona only moves cost between two lines.
        assert abs(with_persona.used - without.used) <= 1
        assert without.of("persona") == 0

    def test_the_three_kinds_of_tool_schema_are_separated(self):
        schemas = [_schema("read_file"), _schema("slack_post"), _schema("my_own_thing")]
        book = ledger.take(
            _turn(),
            schemas,
            persona=PERSONA,
            window=1_000_000,
            mcp_names=("slack_post",),
            custom_names=("my_own_thing",),
        )
        assert book.of("built_in_tools") > 0
        assert book.of("mcp_tools") > 0
        assert book.of("custom_tools") > 0

    def test_the_tool_block_is_counted_at_all(self):
        """The old hand-rolled sum added up `content` lengths and missed 11,000 tokens of
        schemas — the largest fixed cost in the prompt."""
        bare = ledger.take(_turn(), persona=PERSONA, window=1_000_000)
        with_tools = ledger.take(
            _turn(), [_schema(f"t{i}", 400) for i in range(40)], persona=PERSONA, window=1_000_000
        )
        assert with_tools.used > bare.used

    def test_a_picture_is_not_counted_as_conversation(self):
        turn = [
            *_turn(),
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "here:"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                ],
            },
        ]
        book = ledger.take(turn, persona=PERSONA, window=1_000_000)
        assert book.of("images") > 0


class TestTheArithmetic:
    def test_it_costs_with_the_calibrated_ratio(self):
        """`ContextBudget` learns the real chars-per-token from what the provider charged. A
        ledger on the seed ratio is a guess; the point of showing someone a number is that it
        matches their bill."""
        loose = ledger.take(_turn(), persona=PERSONA, window=1_000_000, chars_per_token=6.0)
        tight = ledger.take(_turn(), persona=PERSONA, window=1_000_000, chars_per_token=2.0)
        assert tight.used > loose.used

    def test_free_and_share_agree_with_used(self):
        book = ledger.take(_turn(), persona=PERSONA, window=1_000_000)
        assert book.free == 1_000_000 - book.used
        assert 0 < book.share < 1

    def test_past_is_false_when_the_window_is_unknown(self):
        """0 means "cannot say", and every guard in this codebase treats a guessed window as
        more dangerous than no window. A `past()` that returned True here would fold a
        two-message conversation on a local model."""
        book = ledger.take(_turn(), persona=PERSONA, window=0)
        assert book.past(0.8) is False
        assert book.past(0.0) is False
        assert book.share == 0.0
        assert book.used > 0  # the absolute numbers are still real

    def test_past_fires_above_the_share_and_not_below(self):
        big = [{"role": "tool", "tool_name": "read_file", "content": "x" * 900_000}]
        book = ledger.take(big, window=1_000_000, chars_per_token=1.0)
        assert book.past(0.8) is True
        assert book.past(0.95) is False


class TestTheWireShape:
    def test_empty_categories_are_dropped(self):
        """The meter should not render eight zeroes."""
        wire = ledger.take(_turn(), persona=PERSONA, window=1_000_000).as_wire()
        assert all(line["tokens"] > 0 for line in wire["lines"])
        assert {"window", "used", "free", "share", "lines"} <= set(wire)

    def test_it_survives_json(self):
        wire = ledger.take(_turn(), persona=PERSONA, window=1_000_000).as_wire()
        assert json.loads(json.dumps(wire)) == wire

    def test_the_lines_add_up_to_used(self):
        book = ledger.take(_turn(), [_schema("read_file")], persona=PERSONA, window=1_000_000)
        assert sum(line["tokens"] for line in book.as_wire()["lines"]) == book.used
