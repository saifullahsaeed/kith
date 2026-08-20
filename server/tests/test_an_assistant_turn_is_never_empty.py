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

            # `prompt`, not `chat`. The guard lives in `_build_messages`, which moved to
            # `services/turn/prompt.py` — and this assertion would have gone on passing
            # against a module that no longer contained the code, because it only asks
            # whether the text appears *somewhere* in the file it is handed.
            from kith.services.turn import prompt

            source = inspect.getsource(prompt)
            assert 'message.get("role") == "assistant" and not str' in source
            return
        roles = [m["role"] for m in built if m["role"] != "system"]
        assert roles == ["user", "user"]
