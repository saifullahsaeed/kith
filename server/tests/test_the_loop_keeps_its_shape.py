"""Two invariants of the round loop that nothing asserted, and both fail silently.

`_run_turn` is 340 lines at cyclomatic complexity 40 and is due to be split up. These are the
tests that make that safe: they pass on the function as it stands, and they fail on the two
mistakes a decomposition is most likely to make. Neither would be caught by anything else —
the turn still completes, the answer still arrives, and only a number on a meter is wrong.

**The meter must describe the request that is actually about to be sent.** `schemas` is
rebound twice a round — by the phase's `allow` set and by the landing filter — and the reading
is taken *after* both. Move the reading earlier, or hand a collaborator the pre-narrowing list,
and a landing round reports the cost of a toolset it did not send.

Measured, so the assertion has a real threshold rather than a guessed one: with two schemas
offered and one surviving the landing filter, the reading is 25 tokens narrowed against 50
wide. The test runs both and compares, because an absolute bound loose enough to be robust was
also loose enough to pass when the invariant was deliberately broken — which is how the first
version of this file failed to catch anything.

What it does *not* pin is `take_reading`'s default-argument binding
(`def take_reading(schemas: list[dict] = schemas)`). That guards against a reading outliving
its round, which cannot happen today, so no behavioural test can see it. Its comment explains
itself; this is not that test.

**The per-round event order is a contract.** `context` is emitted *after* room has been made
and *before* the request goes out, because it is the reading of the request about to be sent —
`api/routes/chat._MindFeed` and the composer's meter both read it that way. A decomposition
that yields it from inside a collaborator, or that emits `stats` before the deltas it belongs
to, changes what the interface shows without changing what the model does.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith.config import Config
from kith.domain.tooling import ToolHost
from kith.services import agent_loop


def _config() -> Config:
    return Config(
        model="m",
        num_ctx=40_000,
        num_predict=1_000,
        system="",
        think=False,
        context_window=40_000,
    )


def _schema(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "description": "", "parameters": {}}}


#: One tool the landing filter keeps, and one it does not. `update_task` records what this turn
#: did, which is what landing is for; `web_search` is in `_GATHERING_TOOLS` and is taken away,
#: because the reserve exists to stop a turn gathering forever.
_KEPT = "update_task"
_DROPPED = "web_search"


@pytest.fixture
def host() -> ToolHost:
    """A tool layer offering one landing tool and one work tool, and running neither."""
    return ToolHost(
        schemas=lambda only=None, mcp=None: [_schema(_KEPT), _schema(_DROPPED)],
        run=lambda name, arguments, allow=None: {"ok": True, "result": "done"},
    )


class TestTheMeterDescribesTheRoundItIsIn:
    def test_a_landing_round_is_costed_on_less_than_a_working_one(self, db: Path, monkeypatch, host):
        """The same toolset, two phases, two readings — and the narrowed one must be smaller.

        Comparative rather than absolute on purpose. The landing round offers one of the two
        schemas, so its tools figure is half the working round's (25 against 50, measured). A
        bound picked by eye passed in both directions when the invariant was broken by hand;
        this cannot.
        """

        def once(rounds: int) -> tuple[list[str], int]:
            seen: list[list[str]] = []

            def fake_stream(convo, config, host_url, tools=None, tool_choice="auto", routing=None):
                seen.append(sorted(t["function"]["name"] for t in (tools or [])))
                yield {"type": "turn", "content": "done", "tool_calls": [], "stats": None}

            monkeypatch.setattr(agent_loop, "_stream_once", fake_stream)
            events = list(agent_loop._run_turn([], _config(), "", db, host, max_rounds=rounds))
            reading = next(e["context"] for e in events if e["type"] == "context")
            line = next(one for one in reading["lines"] if one["key"] == "built_in_tools")
            return seen[0], line["tokens"]

        # max_rounds=2 puts the reserve over both rounds, so landing is on from the first.
        landing_tools, landing_cost = once(2)
        working_tools, working_cost = once(40)

        assert landing_tools == [_KEPT], "the landing filter should have dropped the work tool"
        assert working_tools == [_KEPT, _DROPPED], "a working round offers everything it has"
        assert landing_cost < working_cost, (
            f"the landing round was costed at {landing_cost} against the working round's "
            f"{working_cost} — it is being measured on a toolset it did not send, which means "
            "the reading is being taken before the narrowing rather than after"
        )


class TestTheRoundReportsInOneOrder:
    def test_the_reading_comes_before_the_request_and_the_stats_after(self, db: Path, monkeypatch, host):
        """`context` describes the request about to go out, so it precedes the deltas; `stats`
        is what that request cost, so it follows them. The interface reads it in that order."""

        def fake_stream(convo, config, host_url, tools=None, tool_choice="auto", routing=None):
            yield {"type": "delta", "role": "text", "text": "hi"}
            yield {
                "type": "turn",
                "content": "hi",
                "tool_calls": [],
                "stats": {"promptTokens": 10, "completionTokens": 2},
            }

        monkeypatch.setattr(agent_loop, "_stream_once", fake_stream)
        kinds = [e["type"] for e in agent_loop._run_turn([], _config(), "", db, host, max_rounds=40)]

        assert kinds.index("context") < kinds.index("delta"), (
            "the reading must describe the request before it is sent, not after"
        )
        assert kinds.index("delta") < kinds.index("stats"), (
            "stats is what the request cost — it cannot precede the answer it paid for"
        )

    def test_a_tool_call_is_reported_before_its_result(self, db: Path, monkeypatch, host):
        """Pairing on the wire is positional for the interface, so the order is the contract."""
        rounds = {"n": 0}

        def fake_stream(convo, config, host_url, tools=None, tool_choice="auto", routing=None):
            rounds["n"] += 1
            if rounds["n"] == 1:
                yield {
                    "type": "turn",
                    "content": "",
                    "stats": None,
                    "tool_calls": [{"id": "c0", "function": {"name": _KEPT, "arguments": "{}"}}],
                }
            else:
                yield {"type": "turn", "content": "done", "tool_calls": [], "stats": None}

        monkeypatch.setattr(agent_loop, "_stream_once", fake_stream)
        kinds = [e["type"] for e in agent_loop._run_turn([], _config(), "", db, host, max_rounds=40)]

        assert "tool_call" in kinds and "tool_result" in kinds
        assert kinds.index("tool_call") < kinds.index("tool_result")
