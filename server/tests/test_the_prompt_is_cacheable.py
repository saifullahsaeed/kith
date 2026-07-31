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

from kith.api.routes.chat import _build_messages
from kith.config import default_config
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
        from kith.config import AGENT_DB_PATH

        first = [t["function"]["name"] for t in tool_schemas(AGENT_DB_PATH)]
        again = [t["function"]["name"] for t in tool_schemas(AGENT_DB_PATH)]
        # Tools are part of the cached prefix, so a reordering between rounds would throw
        # the cache away as surely as an edit would.
        assert first == again
