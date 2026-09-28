"""The next turn's prompt begins with exactly what this turn sent.

A prompt cache is a prefix. Within a turn the prefix held — 99% cached from the second round on —
and across turns it did not: the first round of a new turn was 23% cached across two weeks, and
on DeepSeek's own host 87 of 89 turns opened with nothing cached past the ~19k-token head. The
history had not changed. What changed was *how it was rebuilt*: the next turn replays this one
from the transcript, and the replay was a different prompt from the one this turn sent —

* the present-state block was a trailing system message that was never written down, so it
  vanished from the replay, and a host that gathers system messages to the top put the per-turn
  clock straight after the head;
* a batched round went out as one message and came back as one message per call;
* ids were the provider's, then minted; arguments were the model's string, then a re-dump;
* a round's preamble was dropped from its call and glued onto the closing answer.

Each of those is a message where the two stop matching, and everything after it is re-billed.
Held here with a real turn loop and a real transcript: turn one's last request is a prefix of
turn two's first, byte for byte.
"""

from __future__ import annotations

import json

import pytest

from kith.api.routes.chat import _record_present, _Recorder
from kith.config import default_config
from kith.domain.tooling import ToolHost
from kith.llm.openai_compat import CONTEXT_OPEN, _to_openai
from kith.services import agent_loop, conversations
from kith.services.turn import prompt as turn_prompt

_SCHEMA = [
    {
        "type": "function",
        "function": {"name": "look", "description": "Look at something.", "parameters": {"type": "object"}},
    }
]


def _host() -> ToolHost:
    return ToolHost(
        schemas=lambda **_k: _SCHEMA,
        run=lambda name, args, allow=None: {"ok": True, "result": f"{name} saw {json.dumps(args)} " * 30},
    )


def _model(script: list[tuple[str, list[tuple[str, dict]]]], sent: list[list[dict]]):
    """A model that answers from a script, and remembers every request exactly as it went out.

    Calls come back the way a provider sends them: its own ids, and argument strings in its own
    spacing. Both are things the transcript does not keep.
    """
    asked = [0]

    def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
        sent.append(json.loads(json.dumps(_to_openai(convo))))
        text, calls = script[min(asked[0], len(script) - 1)]
        asked[0] += 1
        if text:
            yield {"type": "delta", "role": "text", "text": text}
        yield {
            "type": "turn",
            "content": text,
            "tool_calls": [
                {
                    "id": f"provider-{asked[0]}-{i}",
                    "function": {"name": name, "arguments": json.dumps(args, indent=2)},
                }
                for i, (name, args) in enumerate(calls)
            ],
            "stats": {"promptTokens": 10, "cachedTokens": 0, "costUsd": 0.0},
        }

    return fake


def _turn(db, conversation_id: str, said: str, script, monkeypatch, sent) -> None:
    conversations.record(db, conversation_id, "user", said)
    messages = turn_prompt._build_messages(
        conversations.full_messages(conversation_id), default_config(), conversation_id
    )
    _record_present(conversation_id, messages)
    monkeypatch.setattr(agent_loop, "_stream_once", _model(script, sent))
    recorder = _Recorder(conversation_id)
    for event in agent_loop._run_turn(
        messages, default_config(), "host", db, _host(), max_rounds=30, landing_reserve=0
    ):
        recorder.saw(event)
    recorder.finish()


@pytest.fixture
def two_turns(db, monkeypatch):
    monkeypatch.setattr(turn_prompt, "AGENT_DB_PATH", db)
    conversation = conversations.start(db, "a chat")
    cid = conversation["id"]
    sent: list[list[dict]] = []
    _turn(
        db,
        cid,
        "what is in the config?",
        [
            ("Let me look at both.", [("look", {"at": "config"}), ("look", {"at": "env"})]),
            ("", [("look", {"at": "secrets"})]),
            ("It sets the port to 8611.", []),
        ],
        monkeypatch,
        sent,
    )
    first_turn = len(sent)
    _turn(db, cid, "and the host?", [("localhost.", [])], monkeypatch, sent)
    return sent[first_turn - 1], sent[first_turn]


class TestTheReplayIsWhatWasSent:
    def test_the_last_request_of_a_turn_is_a_prefix_of_the_next_turns_first(self, two_turns):
        last, first = two_turns
        for index, message in enumerate(last):
            assert first[index] == message, f"the two prompts stop matching at message {index}"

    def test_the_closing_answer_is_the_last_rounds_words_only(self, two_turns):
        _, first = two_turns
        answers = [m for m in first if m["role"] == "assistant" and not m.get("tool_calls")]
        assert answers[-1]["content"] == "It sets the port to 8611."

    def test_the_preamble_rides_on_its_own_call(self, two_turns):
        _, first = two_turns
        calling = [m for m in first if m["role"] == "assistant" and m.get("tool_calls")]
        assert calling[0]["content"] == "Let me look at both."
        assert all(len(m["tool_calls"]) == 1 for m in calling)


class TestThePresentStateRidesInTheUserMessage:
    def test_no_system_message_follows_the_head(self, two_turns):
        _, first = two_turns
        assert [m["role"] for m in first].count("system") == 1, "only the persona may be a system message"

    def test_each_turn_carries_its_own_block_in_its_own_message(self, two_turns):
        _, first = two_turns
        asked = [m for m in first if m["role"] == "user"]
        assert len(asked) == 2
        assert all(CONTEXT_OPEN in m["content"] for m in asked)
        assert asked[0]["content"].startswith("what is in the config?")


class TestTheMeterFilesItCorrectly:
    def test_an_earlier_turns_block_is_conversation_not_persona(self, db, monkeypatch):
        from kith.llm import ledger

        monkeypatch.setattr(turn_prompt, "AGENT_DB_PATH", db)
        cid = conversations.start(db, "a chat")["id"]
        conversations.record(db, cid, "user", "first")
        conversations.record_event(cid, "present", {"text": "[Right now]\nAn old clock."})
        conversations.record(db, cid, "assistant", "ok")
        conversations.record(db, cid, "user", "second")
        built = turn_prompt._build_messages(conversations.full_messages(cid), default_config(), cid)
        alone = turn_prompt._build_messages(
            [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "ok"},
                {"role": "user", "content": "second"},
            ],
            default_config(),
            cid,
        )
        with_old = ledger.take(built, window=1_000_000, chars_per_token=1.0)
        without = ledger.take(alone, window=1_000_000, chars_per_token=1.0)
        assert with_old.of("persona") == without.of("persona")
        assert with_old.of("system") == without.of("system")
