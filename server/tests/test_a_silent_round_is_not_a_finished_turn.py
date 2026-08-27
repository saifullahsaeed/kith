"""A round that comes back with nothing in it is not the model finishing.

Off a real conversation — 2026-08-20, `20260820-103109065-11b95e.jsonl`. His person asked
"can you improve it please" and got nothing. Then "?". Then "what are you doing man", three
times in four minutes. The transcript says why: three consecutive rounds on
`google/gemini-3.7-flash` recorded

    responseTokens=1468  reasoningTokens=1468
    responseTokens=1281  reasoningTokens=1281
    responseTokens=1528  reasoningTokens=1528

— the entire response spent on reasoning, no content and no tool call — and each round has a
`reasoning` event, a `stats` event, and then nothing. No `said`, no assistant message, no
error. The loop read "no tool calls" as "he's finished talking" and returned.

The two cases are indistinguishable at that branch and must not be: one is a complete answer,
the other is silence, and silence returned quietly is the loop looking hung from a chair.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith import tools
from kith.config import default_config
from kith.services import agent_loop


def _rounds(*responses: dict):
    """One canned `turn` event per round; the last one repeats if asked for again."""
    calls: list[int] = []

    def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
        calls.append(1)
        yield dict(responses[min(len(calls) - 1, len(responses) - 1)], type="turn")

    return fake, calls


def _empty(reasoning_tokens: int = 1468) -> dict:
    """What a reasoning-only response looks like once the stream is parsed."""
    return {
        "content": "",
        "tool_calls": [],
        "stats": {"responseTokens": reasoning_tokens, "reasoningTokens": reasoning_tokens},
    }


def _spoke(text: str = "here it is") -> dict:
    return {"content": text, "tool_calls": [], "stats": {"responseTokens": 12}}


def _run(fake, monkeypatch, db: Path, max_rounds: int = 6):
    monkeypatch.setattr(agent_loop, "_stream_once", fake)
    monkeypatch.setattr(agent_loop.time, "sleep", lambda _s: None)
    return list(
        agent_loop._run_turn(
            [], default_config(), "host", db, tools.host(db, language_server=False), max_rounds=max_rounds
        )
    )


class TestOneSilentRound:
    def test_it_is_tried_again(self, db: Path, monkeypatch):
        fake, calls = _rounds(_empty(), _spoke())
        events = _run(fake, monkeypatch, db)

        assert len(calls) == 2, "an empty response is a round to go again on, not a turn to end"
        assert not [e for e in events if e["type"] == "error"], "one blip is absorbed silently"

    def test_the_turn_still_answers(self, db: Path, monkeypatch):
        fake, _calls = _rounds(_empty(), _spoke("here it is"))
        events = _run(fake, monkeypatch, db)

        said = [e for e in events if e["type"] == "delta" and e.get("role") == "text"]
        # The loop forwards deltas from the stream; the canned stream has none, so assert on
        # what the loop itself decided instead: it did not stop on the empty round.
        assert not said or "here it is" in "".join(e["text"] for e in said)
        assert len([e for e in events if e["type"] == "stats"]) == 2

    def test_the_directive_says_the_request_got_through(self, db: Path, monkeypatch):
        """Not `_ROUND_FAILED_DIRECTIVE` — nothing failed — and not the landing nudge, which
        narrows the toolset and claims he is out of budget."""
        sent: list[list[dict]] = []

        def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
            sent.append([dict(m) for m in convo])
            yield dict(_empty() if len(sent) == 1 else _spoke(), type="turn")

        _run(fake, monkeypatch, db)

        nudge = sent[1][-1]
        # `system`, not `user`. The harness is talking, and it used to do so wearing his person's
        # role — which is a lie the model can act on: the one time a directive landed at an odd
        # moment he answered it as though a person had said it ("I haven't been researching
        # anything this turn"). See `agent_loop._directive`.
        assert nudge["role"] == "system"
        assert nudge["_directive"] is True
        assert nudge["content"] == agent_loop._EMPTY_ROUND_DIRECTIVE
        assert "no answer and no tool call" in nudge["content"]
        assert nudge["content"] != agent_loop._ROUND_FAILED_DIRECTIVE
        assert nudge["content"] != agent_loop._LANDING_DIRECTIVE


class TestTwoInARow:
    def test_the_turn_says_so_out_loud(self, db: Path, monkeypatch):
        """The bug in one assertion: this used to end with no error and nothing said."""
        fake, calls = _rounds(_empty())
        events = _run(fake, monkeypatch, db)

        errors = [e for e in events if e["type"] == "error"]
        assert errors, "a turn that produced nothing must not end quietly"
        assert len(calls) == 2, "and must not keep paying for rounds that say nothing"

    def test_the_error_names_the_model(self, db: Path, monkeypatch):
        """Because the fix is to switch model, and the person cannot do that without knowing
        which one went quiet — four different models served this one conversation."""
        fake, _calls = _rounds(_empty())
        config = default_config()
        monkeypatch.setattr(agent_loop, "_stream_once", fake)
        monkeypatch.setattr(agent_loop.time, "sleep", lambda _s: None)
        events = list(
            agent_loop._run_turn([], config, "host", db, tools.host(db, language_server=False), max_rounds=6)
        )

        message = next(e for e in events if e["type"] == "error")["message"]
        assert config.model in message
        assert "empty response" in message


class TestWhatIsNotAffected:
    def test_a_plain_answer_still_ends_the_turn(self, db: Path, monkeypatch):
        """Saying "why what?" is a complete answer. It must cost exactly one round."""
        fake, calls = _rounds(_spoke("why what?"))
        events = _run(fake, monkeypatch, db)

        assert len(calls) == 1
        assert not [e for e in events if e["type"] == "error"]

    def test_a_tool_call_with_no_preamble_is_not_empty(self, db: Path, monkeypatch):
        """The round that *is* the tool call carries no content, and always has — reading it
        as silence would break every turn that does real work."""
        call = {"id": "c1", "function": {"name": "read_file", "arguments": "{}"}}
        fake, calls = _rounds(
            {"content": "", "tool_calls": [call], "stats": {}},
            _spoke("done"),
        )
        events = _run(fake, monkeypatch, db)

        assert len(calls) == 2
        assert not [e for e in events if e["type"] == "error"]

    def test_whitespace_only_is_silence(self, db: Path, monkeypatch):
        """A lone newline is not an answer, and a provider that emits one should not be able
        to end the turn with it."""
        fake, calls = _rounds({"content": "\n  \n", "tool_calls": [], "stats": {}})
        events = _run(fake, monkeypatch, db)

        assert len(calls) == 2
        assert [e for e in events if e["type"] == "error"]


@pytest.mark.parametrize("streak", [1, 2, 3])
def test_the_counter_resets_on_a_round_that_spoke(db: Path, monkeypatch, streak: int):
    """The question is "is this model answering at all?", so an empty round three turns ago
    says nothing about this one — otherwise a long turn accumulates its way into the error."""
    script = []
    for _ in range(streak):
        script += [_empty(), _spoke("...")]
    calls: list[int] = []

    def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
        calls.append(1)
        i = len(calls) - 1
        # Each "spoke" round calls a tool so the turn keeps going, except the last.
        response = script[min(i, len(script) - 1)]
        if response is not script[-1] and response["content"]:
            response = {
                "content": response["content"],
                "tool_calls": [{"id": f"c{i}", "function": {"name": "read_file", "arguments": "{}"}}],
                "stats": {},
            }
        yield dict(response, type="turn")

    monkeypatch.setattr(agent_loop, "_stream_once", fake)
    monkeypatch.setattr(agent_loop.time, "sleep", lambda _s: None)
    events = list(
        agent_loop._run_turn(
            [], default_config(), "host", db, tools.host(db, language_server=False), max_rounds=40
        )
    )

    assert not [e for e in events if e["type"] == "error"], (
        "no two empty rounds ever landed in a row, so the turn should never have given up"
    )


class TestTheNudgeLeavesATrace:
    """A directive used to happen and leave nothing behind.

    It lives in the round's own message list and nowhere else, so afterwards a turn that had been
    told four times to stop gathering looked exactly like one that had never been told anything —
    and `/context`, whose entire job is "show me what was sent", rebuilds the prompt from the
    transcript and so could not show any of them.
    """

    def test_it_is_announced_on_the_stream(self, db: Path, monkeypatch):
        def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
            yield dict(_empty() if not getattr(fake, "seen", False) else _spoke(), type="turn")
            fake.seen = True  # type: ignore[attr-defined]  # a flag on the stub itself

        events = _run(fake, monkeypatch, db)

        directives = [e for e in events if e["type"] == "directive"]
        assert directives, "nothing said the turn had been nudged"
        assert directives[0]["text"] == agent_loop._EMPTY_ROUND_DIRECTIVE

    def test_it_is_not_replayed_into_a_later_turn(self, db: Path, monkeypatch, tmp_path):
        """Recorded, but as a kind `full_messages` does not reconstruct.

        The whole reason a directive is turn-local is that it is about *this* round. A nudge from
        turn 5 appearing in turn 50's history would be the transcript telling him he is out of
        budget on a turn that has not started.
        """
        from kith.services import conversations

        conversations.record_event("nudged-1", "message", {"role": "user", "content": "go"})
        conversations.record_event("nudged-1", "directive", {"text": agent_loop._LANDING_DIRECTIVE})
        conversations.record_event("nudged-1", "message", {"role": "assistant", "content": "done"})

        replayed = conversations.full_messages("nudged-1")

        assert [m["role"] for m in replayed] == ["user", "assistant"]
        assert not any(agent_loop._LANDING_DIRECTIVE in str(m.get("content")) for m in replayed)
