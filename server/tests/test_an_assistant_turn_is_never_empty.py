"""A tool-calling turn with no preamble does not send an empty assistant message.

Two chat turns died thirty seconds apart on:

    400 — the message at position 54 with role 'assistant' must not be empty

Both at zero prompt tokens, so the request never ran. And because the offending message was
already in the turn's own history, retrying rebuilt the same conversation and hit the same
wall — a turn that cannot be retried is simply lost, with a raw provider error where the
answer should be.

Most rounds have a preamble ("let me look at the config"), which is why this survived so
long. The ones that do not were sending `"content": ""` beside the tool calls, and a
provider is entitled to refuse that. The schema has always allowed content to be absent:
a message that *is* the tool call does not need to say anything.
"""

from __future__ import annotations


def assistant_turn(content: str, tool_calls: list) -> dict:
    """Built the way the agent loop builds it."""
    turn: dict = {"role": "assistant", "tool_calls": tool_calls}
    if content:
        turn["content"] = content
    return turn


CALL = [{"id": "c1", "type": "function", "function": {"name": "grep", "arguments": "{}"}}]


class TestTheShapeOnTheWire:
    def test_no_preamble_means_no_content_key(self):
        turn = assistant_turn("", CALL)
        assert "content" not in turn, "an empty string here is what the provider refused"
        assert turn["tool_calls"] == CALL

    def test_a_preamble_is_kept(self):
        turn = assistant_turn("Let me look at the config.", CALL)
        assert turn["content"] == "Let me look at the config."

    def test_the_loop_builds_it_this_way(self, db):
        """The loop, driven, rather than its source read.

        This used to assert that the string `turn["content"] = content` appeared in
        `_run_turn`'s source. The intent was right — the failure only shows against a live
        provider, mid-turn, rarely, so something has to guard it here — but pinning a *local
        variable name* is not that guard. Renaming `turn` (it collided with the frozen per-turn
        settings object) broke this test while the behaviour it protects was untouched, which is
        the opposite of what a regression test is for.

        So: run a round that calls a tool and says nothing, and look at what the next round is
        actually asked to send.
        """
        from kith import tools
        from kith.config import default_config
        from kith.services import agent_loop

        rounds: list[list[dict]] = []

        def silent_call(convo, config, host, tools=None, tool_choice="auto", routing=None):
            rounds.append([dict(m) for m in convo])
            if len(rounds) == 1:
                yield {"type": "turn", "content": "", "tool_calls": CALL, "stats": {}}
            else:
                yield {"type": "turn", "content": "done", "tool_calls": [], "stats": {}}

        agent_loop._stream_once, original = silent_call, agent_loop._stream_once
        try:
            list(
                agent_loop._run_turn(
                    [{"role": "user", "content": "go"}],
                    default_config(),
                    "host",
                    db,
                    tools.host(db, language_server=False),
                    max_rounds=4,
                )
            )
        finally:
            agent_loop._stream_once = original

        assert len(rounds) == 2, "the loop did not run the tool and come back"
        assistant = [m for m in rounds[1] if m.get("role") == "assistant"]
        assert assistant, "the tool-calling message never reached the history"
        assert "content" not in assistant[0], "an empty string here is what the provider refused"
        assert assistant[0]["tool_calls"] == CALL

    def test_a_preamble_still_travels_with_the_call(self, db):
        """The other half: content that exists is not dropped by the same branch."""
        from kith import tools
        from kith.config import default_config
        from kith.services import agent_loop

        rounds: list[list[dict]] = []

        def with_preamble(convo, config, host, tools=None, tool_choice="auto", routing=None):
            rounds.append([dict(m) for m in convo])
            if len(rounds) == 1:
                yield {
                    "type": "turn",
                    "content": "Let me look at the config.",
                    "tool_calls": CALL,
                    "stats": {},
                }
            else:
                yield {"type": "turn", "content": "done", "tool_calls": [], "stats": {}}

        agent_loop._stream_once, original = with_preamble, agent_loop._stream_once
        try:
            list(
                agent_loop._run_turn(
                    [{"role": "user", "content": "go"}],
                    default_config(),
                    "host",
                    db,
                    tools.host(db, language_server=False),
                    max_rounds=4,
                )
            )
        finally:
            agent_loop._stream_once = original

        assistant = [m for m in rounds[1] if m.get("role") == "assistant"]
        assert assistant[0]["content"] == "Let me look at the config."


def _replayed(messages: list[dict]) -> list[dict]:
    """The replayed conversation, without the live-state block `_assemble` appends.

    That trailing system message is rebuilt every turn and is not part of what is being
    replayed, so it is noise for these assertions.
    """
    return [m for m in messages if m.get("role") != "system"]


class TestReplayingHistory:
    def test_an_empty_assistant_message_is_not_replayed(self):
        """Insurance. Nothing writes one today, but a conversation that acquired one would be
        permanently unusable: every later message rebuilds the same history and fails the same
        way, with nothing on screen to say why.

        Against `prompt._assemble`, which is where the guard lives and what both `_build_messages`
        and `as_sent` go through. This used to reach for `chat._history` and fall back to grepping
        `prompt`'s source for a fragment of the condition when it was missing — and `chat._history`
        has not existed for some time, so the grep was all that ever ran. A test that asserts a
        string appears in a source file passes just as well when the line is commented out.
        """
        from kith.services.turn import prompt

        folded = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "   "},
            {"role": "user", "content": "still there?"},
        ]
        built = _replayed(prompt._assemble([], folded, conversation_id=""))
        assert [m["role"] for m in built] == ["user", "user"]
        assert [m["content"] for m in built] == ["hello", "still there?"]

    def test_an_assistant_message_with_real_text_is_replayed(self):
        """The other half, so the test above cannot pass by dropping every assistant turn."""
        from kith.services.turn import prompt

        folded = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "I had something to say"},
        ]
        built = _replayed(prompt._assemble([], folded, conversation_id=""))
        assert [m["role"] for m in built] == ["user", "assistant"]

    def test_a_replayed_tool_call_survives_having_no_content(self):
        """An assistant message carrying `tool_calls` and no text is not an empty turn — it is
        a replayed call, and the guard above must not eat it."""
        from kith.services.turn import prompt

        call = [{"function": {"name": "read_file", "arguments": "{}"}}]
        assembled = prompt._assemble([], [{"role": "assistant", "tool_calls": call}], conversation_id="")
        built = _replayed(assembled)
        assert [m["role"] for m in built] == ["assistant"]
        assert built[0]["tool_calls"] == call
