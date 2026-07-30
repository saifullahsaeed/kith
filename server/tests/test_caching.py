"""Cache breakpoints: where they go, and where they must not.

The cost of getting this wrong is asymmetric. A missing breakpoint on Anthropic means
every round of every turn pays full price forever — measured at $0.0065 per round on a
13.3k prefix that would otherwise read for $0.0027. A breakpoint in the wrong place
means writes that are never read, billed at 1.25x. So both directions are tested.
"""

from __future__ import annotations

import pytest

from kith.llm import caching

BIG = "x" * int(caching.MIN_CACHEABLE_TOKENS * 3.7 + 100)
SMALL = "still the persona, but short"


def system(text: str = BIG) -> dict:
    return {"role": "system", "content": text}


def marked(message: dict) -> bool:
    """Does this message carry a cache breakpoint?"""
    content = message.get("content")
    return isinstance(content, list) and any(
        block.get("cache_control", {}).get("type") == "ephemeral" for block in content
    )


class TestWhichProviders:
    @pytest.mark.parametrize(
        "model",
        [
            "anthropic/claude-opus-5",
            "anthropic/claude-sonnet-5",
            "qwen/qwen3-coder-plus",
            "google/gemini-3.6-flash",
            "ANTHROPIC/Claude-Opus-5",
        ],
    )
    def test_families_that_cache_only_when_asked(self, model):
        # These get nothing without a breakpoint — verified live against the API.
        assert caching.needs_breakpoint(model)

    @pytest.mark.parametrize(
        "model",
        [
            "moonshotai/kimi-k2.7-code",
            "openai/gpt-5.6",
            "deepseek/deepseek-v4-pro",
            "z-ai/glm-5.2",
            "x-ai/grok-4.5",
            "groq/llama-3.3",
        ],
    )
    def test_families_that_cache_on_their_own(self, model):
        # Sending a breakpoint here is harmless but pointless, so it isn't sent.
        assert not caching.needs_breakpoint(model)


class TestPlacement:
    def test_the_system_prefix_is_marked(self):
        out = caching.apply([system(), {"role": "user", "content": "hello"}], "anthropic/claude-sonnet-5")
        assert marked(out[0])

    def test_the_tool_schemas_count_toward_the_minimum(self):
        """They are cached with the system prompt, so they decide whether it qualifies.

        This is the bug that made the whole feature a no-op on its first run: the real
        persona is 9,082 characters — under the 4,096-token minimum on its own — while
        persona plus 51 tool schemas is 32,205 and well over.
        """
        real_persona = "p" * 9_082
        schemas = 23_123  # what tool_schemas() actually serialises to
        alone = caching.apply(
            [system(real_persona), {"role": "user", "content": "x"}], "anthropic/claude-sonnet-5"
        )
        assert not marked(alone[0]), "too small counted on its own"
        with_tools = caching.apply(
            [system(real_persona), {"role": "user", "content": "x"}],
            "anthropic/claude-sonnet-5",
            prefix_extra_chars=schemas,
        )
        assert marked(with_tools[0]), "qualifies once the tools are counted"

    def test_a_prefix_too_small_to_cache_is_left_alone(self):
        # Below the provider minimum the write is refused, and a write that is never
        # read still bills at 1.25x.
        out = caching.apply([system(SMALL), {"role": "user", "content": "hi"}], "anthropic/claude-sonnet-5")
        assert not marked(out[0])

    def test_a_long_conversation_is_also_marked_at_its_tail(self):
        """Round N reads what round N-1 wrote — the tool results are most of a long turn."""
        convo = [
            system(),
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": BIG},
            {"role": "user", "content": "and now?"},
        ]
        out = caching.apply(convo, "anthropic/claude-sonnet-5")
        assert marked(out[0]), "prefix"
        assert marked(out[-1]), "tail"

    def test_a_short_conversation_gets_only_the_prefix(self):
        out = caching.apply([system(), {"role": "user", "content": "hi"}], "anthropic/claude-sonnet-5")
        assert marked(out[0])
        assert not marked(out[-1])

    def test_at_most_two_breakpoints(self):
        # Anthropic allows four; staying well under leaves room and keeps write cost down.
        convo = [system(), *({"role": "assistant", "content": BIG} for _ in range(6))]
        out = caching.apply(convo, "anthropic/claude-sonnet-5")
        assert sum(1 for m in out if marked(m)) <= 2


class TestRestraint:
    def test_an_automatic_provider_gets_an_untouched_payload(self):
        convo = [system(), {"role": "user", "content": BIG}]
        out = caching.apply(convo, "moonshotai/kimi-k2.7-code")
        assert out is convo, "should not even copy"
        assert all(isinstance(m["content"], str) for m in out)

    def test_the_caller_s_messages_are_never_mutated(self):
        """They are the agent loop's live history; a marker leaking in would be re-sent
        as literal content on the next round."""
        convo = [system(), {"role": "assistant", "content": BIG}, {"role": "user", "content": "x"}]
        before = [dict(m) for m in convo]
        caching.apply(convo, "anthropic/claude-sonnet-5")
        assert convo == before

    def test_a_tool_result_is_not_restructured(self):
        """Its tool_call_id pairing is positional; only plain string content is safe."""
        convo = [
            system(),
            {"role": "assistant", "content": BIG},
            {"role": "tool", "tool_call_id": "call_1", "content": "result"},
        ]
        out = caching.apply(convo, "anthropic/claude-sonnet-5")
        assert out[-1]["tool_call_id"] == "call_1"

    def test_an_empty_history_is_returned_as_is(self):
        assert caching.apply([], "anthropic/claude-opus-5") == []


class TestStickiness:
    def test_an_id_is_generated_once_and_remembered(self):
        saved = {}
        first = caching.session_id({}, lambda v: saved.update({caching.SESSION_KEY: v}))
        assert first.startswith("kith-")
        assert saved[caching.SESSION_KEY] == first

    def test_a_stored_id_is_reused_so_a_restart_keeps_the_cache_warm(self):
        def explode(_):
            raise AssertionError("must not overwrite a stored session id")

        assert caching.session_id({caching.SESSION_KEY: "kith-existing"}, explode) == "kith-existing"

    def test_it_fits_openrouter_s_limit(self):
        assert len(caching.session_id({}, lambda v: None)) <= 256


class TestTheStableSeam:
    """The persona is the only region identical between two different requests.

    Everything after it carries the clock, so a breakpoint placed over the whole system
    prompt is rewritten every request and read almost never. These pin the seam, because
    the symptom of losing it is not an error — it is a cache that quietly only ever
    works inside a single turn.
    """

    PERSONA = "p" * 9_082
    VOLATILE = "\n\n[Right now]\nIt's Thursday, 30 Jul 2026, 3:23 PM.\n" + "m" * 8_500
    SCHEMAS = 23_123

    def prompt(self, volatile: str | None = None) -> dict:
        return system(self.PERSONA + (self.VOLATILE if volatile is None else volatile))

    def split(self, message: dict) -> list[str]:
        content = message["content"]
        assert isinstance(content, list), "expected blocks, got one undivided string"
        return [block["text"] for block in content]

    def apply(self, message: dict) -> dict:
        return caching.apply(
            [message, {"role": "user", "content": "x"}],
            "anthropic/claude-sonnet-5",
            prefix_extra_chars=self.SCHEMAS,
            persona=self.PERSONA,
        )[0]

    def test_the_prompt_is_split_where_the_persona_ends(self):
        blocks = self.split(self.apply(self.prompt()))
        assert blocks[0] == self.PERSONA
        assert blocks[1] == self.VOLATILE

    def test_the_stable_block_is_what_carries_the_breakpoint(self):
        content = self.apply(self.prompt())["content"]
        assert content[0]["cache_control"] == {"type": "ephemeral"}

    def test_the_model_reads_exactly_the_same_prompt(self):
        """Blocks concatenate, so splitting must not add, drop or move a byte."""
        original = self.prompt()
        assert "".join(self.split(self.apply(original))) == original["content"]

    def test_the_stable_block_survives_a_change_in_the_volatile_one(self):
        """The whole point: two requests a minute apart share a cacheable prefix."""
        first = self.split(self.apply(self.prompt()))
        later = self.split(self.apply(self.prompt("\n\n[Right now]\nIt's 4:11 PM.\n" + "z" * 8_500)))
        assert first[0] == later[0], "the persona must be byte-identical between requests"
        assert first[1] != later[1], "and the volatile part is expected to differ"

    def test_a_prompt_that_does_not_start_with_the_persona_is_left_whole(self):
        """An overridden persona, or a caller assembling its own prompt."""
        content = self.apply(system("something else entirely" + "x" * 20_000))["content"]
        assert len(content) == 1, "no seam to split on"
        assert content[0]["cache_control"] == {"type": "ephemeral"}

    def test_a_persona_too_small_to_cache_alone_is_not_split_off(self):
        # Splitting a sub-minimum head off would trade a cache that works within the
        # turn for one the provider refuses outright.
        out = caching.apply(
            [system("tiny" + "v" * 20_000), {"role": "user", "content": "x"}],
            "anthropic/claude-sonnet-5",
            persona="tiny",
        )
        assert len(out[0]["content"]) == 1

    def test_the_volatile_block_is_marked_too(self):
        """It is stale next minute, but the later rounds of this turn re-read it."""
        content = self.apply(self.prompt())["content"]
        assert content[1]["cache_control"] == {"type": "ephemeral"}

    def test_at_most_four_breakpoints(self):
        """Anthropic's hard limit; a fifth is a 400 on the whole request."""
        convo = [
            self.prompt(),
            {"role": "user", "content": BIG},
            {"role": "assistant", "content": BIG},
            {"role": "user", "content": BIG},
        ]
        out = caching.apply(
            convo, "anthropic/claude-sonnet-5", prefix_extra_chars=self.SCHEMAS, persona=self.PERSONA
        )
        breakpoints = sum(
            1
            for message in out
            if isinstance(message.get("content"), list)
            for block in message["content"]
            if "cache_control" in block
        )
        assert breakpoints <= 4, f"{breakpoints} breakpoints"

    def test_the_persona_is_found_even_when_the_caller_stripped_it(self):
        """chat.py strips before assembling; runner.py does not."""
        assert caching.stable_head("abc\n\nrest", "  abc  ") == 3
        assert caching.stable_head("abc\n\nrest", "abc") == 3
        assert caching.stable_head("abc\n\nrest", "different") == 0
        assert caching.stable_head("abc", "") == 0
