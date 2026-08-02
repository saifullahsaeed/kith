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

    def test_the_loop_builds_it_this_way(self):
        """Reads the source, because the shape is the fix and a refactor could undo it
        silently — the failure only appears against a live provider, mid-turn, rarely."""
        import inspect

        from kith.services import agent_loop

        source = inspect.getsource(agent_loop.stream_agent)
        assert '{"role": "assistant", "content": content, "tool_calls": tool_calls}' not in source
        assert 'turn["content"] = content' in source


class TestReplayingHistory:
    def test_an_empty_assistant_message_is_not_replayed(self):
        """Insurance. Nothing writes one today, but a conversation that acquired one would be
        permanently unusable: every later message rebuilds the same history and fails the
        same way, with nothing on screen to say why."""
        from kith.api.routes import chat

        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "   "},
            {"role": "user", "content": "still there?"},
        ]
        built = chat._history(history, conversation_id="") if hasattr(chat, "_history") else None
        if built is None:
            import inspect

            source = inspect.getsource(chat)
            assert 'message.get("role") == "assistant" and not str' in source
            return
        roles = [m["role"] for m in built if m["role"] != "system"]
        assert roles == ["user", "user"]
