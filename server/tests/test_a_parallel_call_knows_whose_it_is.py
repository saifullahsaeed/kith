"""A tool call that shares a round with another must still know which conversation it belongs to.

`ThreadPoolExecutor` does not carry context variables across. A pool thread starts on the
defaults, so `session_context.current()` inside a batched tool call returns `""` — which is
not an error anywhere, because `""` is this codebase's documented, legitimate answer for "a
script, a test, a step nobody is working". Every reader of it therefore does the wrong thing
quietly: `infra/workspace/paths` resolves a relative path against the wrong folder,
`services/touched` records nothing, a permission request surfaces under no conversation.

It cost nothing while the parallel set was four web searches, none of which ask who is
calling. `delegate_subtask` asks — it runs a whole agent loop, and that loop's tools read the
workspace root, the project binding and the turn scratch — so this is a prerequisite for
sending several scouts out in one round rather than a tidy-up.
"""

from __future__ import annotations

from pathlib import Path

from kith import tools
from kith.config import default_config
from kith.kernel import session_context
from kith.services import agent_loop


def _a_call(name: str, **arguments):
    import json

    return {"function": {"name": name, "arguments": json.dumps(arguments)}}


class TestABatchedCallCarriesTheSession:
    def test_two_calls_in_one_round_both_know_the_conversation(self, db: Path, monkeypatch):
        seen: list[str] = []

        def spy(name, arguments, allow=None):
            seen.append(session_context.current())
            return {"ok": True, "result": "fine"}

        # Two parallel-safe calls, so the loop batches them into the pool rather than
        # running them one at a time on this thread — which is the only path that ever lost
        # the context.
        rounds = [
            ("", [_a_call("web_search", query="a"), _a_call("web_search", query="b")]),
            ("done", []),
        ]
        calls: list[int] = []

        def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
            calls.append(1)
            text, tool_calls = rounds[min(len(calls) - 1, len(rounds) - 1)]
            yield {"type": "turn", "content": text, "tool_calls": list(tool_calls), "stats": {}}

        monkeypatch.setattr(agent_loop, "_stream_once", fake)

        host = tools.host(db, language_server=False, mcp=[])
        host = host.__class__(schemas=host.schemas, run=spy, mcp_names=host.mcp_names)

        with session_context.working_in("conv-1"):
            list(
                agent_loop._run_turn(
                    [], default_config(), "host", db, host, max_rounds=4, conversation_id="conv-1"
                )
            )

        assert len(seen) == 2, "both calls should have run"
        assert seen == ["conv-1", "conv-1"], "a pooled call ran as work belonging to nobody"

    def test_a_lone_call_was_never_the_problem(self, db: Path, monkeypatch):
        """The single-call path runs on this thread and always saw the context. Asserted so a
        later refactor that routes everything through the pool cannot quietly lose it."""
        seen: list[str] = []

        def spy(name, arguments, allow=None):
            seen.append(session_context.current())
            return {"ok": True, "result": "fine"}

        rounds = [("", [_a_call("web_search", query="a")]), ("done", [])]
        calls: list[int] = []

        def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
            calls.append(1)
            text, tool_calls = rounds[min(len(calls) - 1, len(rounds) - 1)]
            yield {"type": "turn", "content": text, "tool_calls": list(tool_calls), "stats": {}}

        monkeypatch.setattr(agent_loop, "_stream_once", fake)

        host = tools.host(db, language_server=False, mcp=[])
        host = host.__class__(schemas=host.schemas, run=spy, mcp_names=host.mcp_names)

        with session_context.working_in("conv-1"):
            list(
                agent_loop._run_turn(
                    [], default_config(), "host", db, host, max_rounds=4, conversation_id="conv-1"
                )
            )

        assert seen == ["conv-1"]
