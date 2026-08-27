"""Consecutive rounds of a turn send a stable prefix.

Prompt caching is strictly prefix-based: one changed byte early costs everything after it.
So the property worth holding is not "the prompt is small" but "round N+1 is round N plus
something on the end", and that is checkable without a provider.

Written after chasing a number the wrong way. A live turn showed ~7,600 prompt tokens read
fresh every round, and three explanations were tried and disproved in turn — the tool
schemas (they cache at 98.7% on an identical repeat), the volatile state block (54
characters), and the tool list changing mid-turn (it does not; the 59-to-14 that suggested
it was a probe running with max_rounds=3, where the landing reserve fires immediately).

What is left is provider-side: 31,054 of 38,668 cached is an 80% hit rate, and the residue
is the growing tail plus whatever granularity the provider caches at. That is not a bug in
the request. These tests hold the part that *is* ours.
"""

from __future__ import annotations

import json

from kith.config import default_config
from kith.services.turn.prompt import _build_messages
from kith.tools import tool_schemas


def _prompt(history):
    return _build_messages(history, default_config(), "")


class TestTheMessageListGrowsFromTheEnd:
    def test_a_later_round_is_the_earlier_one_plus_more(self):
        first = _prompt([{"role": "user", "content": "hello"}])
        later = _prompt(
            [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
                {"role": "user", "content": "and again"},
            ]
        )
        # Every message the first prompt had, unchanged and in order, at the front of the
        # second. Insert something in the middle and the whole cache after it is lost.
        shared = [json.dumps(m, sort_keys=True) for m in first[:2]]
        assert [json.dumps(m, sort_keys=True) for m in later[:2]] == shared

    def test_the_persona_message_is_byte_identical_across_prompts(self):
        one = _prompt([{"role": "user", "content": "a"}])[0]
        two = _prompt([{"role": "user", "content": "b"}])[0]
        # The one region that can be cached across turns and ticks rather than only within
        # one. Anything volatile joining it costs the whole prefix on every request.
        assert one == two

    def test_nothing_volatile_leaked_into_the_persona_message(self):
        body = _prompt([{"role": "user", "content": "a"}])[0]["content"]
        import re

        # A clock, a date or a "3 minutes ago" in here is the specific mistake this guards:
        # it changes between requests, so it invalidates everything behind it.
        assert not re.search(r"\b\d{1,2}:\d{2}\b", body), "a time of day is in the cached prefix"
        assert not re.search(r"\b\d+ (seconds?|minutes?|hours?) ago\b", body)


class TestTheToolListIsStableWithinATurn:
    def test_the_same_scope_gives_the_same_tools_in_the_same_order(self):

        first = [t["function"]["name"] for t in tool_schemas()]
        again = [t["function"]["name"] for t in tool_schemas()]
        # Tools are part of the cached prefix, so a reordering between rounds would throw
        # the cache away as surely as an edit would.
        assert first == again


class TestReplayedToolCallsSurviveTheBuild:
    """`conversations.full_messages` now puts tool_calls/tool-shaped messages into what
    gets built here — a real gap found while planning that fix: the per-message loop was
    written for prose-only history and would otherwise drop a `tool`-role message outright,
    drop a `tool_calls`-bearing assistant message for having no `content`, and even a
    message that survived both would then be flattened to bare `{"role","content"}` by
    `_with_attachments`. None of that is hypothetical — it's exactly what the loop did
    before this test existed."""

    def test_a_replayed_call_and_its_result_keep_their_shape(self):
        history = [
            {"role": "user", "content": "read config.py"},
            {
                "role": "assistant",
                "tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "config.py"}}}],
            },
            {"role": "tool", "tool_name": "read_file", "content": json.dumps("DEBUG=True")},
            {"role": "assistant", "content": "It's set to debug mode."},
        ]

        built = _prompt(history)

        calls = [m for m in built if m.get("role") == "assistant" and m.get("tool_calls")]
        tools = [m for m in built if m.get("role") == "tool"]
        assert len(calls) == 1
        assert calls[0]["tool_calls"] == history[1]["tool_calls"]
        assert len(tools) == 1
        assert tools[0] == {"role": "tool", "tool_name": "read_file", "content": json.dumps("DEBUG=True")}
        # And the ordinary text either side of it survived unchanged, same as ever. Last is
        # the fresh "present state" system message `_build_messages` always appends; the
        # turn's own last word is the one before it.
        assert built[-2] == {"role": "assistant", "content": "It's set to debug mode."}

    def test_attachments_on_an_ordinary_message_still_work(self):
        """The fix that made room for tool_calls must not have broken the path it sits
        beside — an ordinary attachment still reaches `_with_attachments` and gets
        rendered, unaffected by the new branches ahead of it in the loop."""
        history = [{"role": "user", "content": "what is this", "attachments": []}]
        built = _prompt(history)
        assert built[-2] == {"role": "user", "content": "what is this"}
