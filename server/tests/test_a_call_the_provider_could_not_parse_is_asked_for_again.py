"""A round Gemini ended with MALFORMED_FUNCTION_CALL was a call, not a silence.

Off a real conversation — 2026-09-30, `20260930-175514809-db351f.jsonl`. Three turns in a row
died on "google/gemini-3.8-flash returned an empty response 2 times in a row — all reasoning,
no answer and no tool call". His person typed "go mf", then "?". OpenRouter's log of the same
rounds says `finish_reason: error`, `native_finish_reason: MALFORMED_FUNCTION_CALL`, nothing
billed: each time the model had started a tool call — its reasoning says which one — and Gemini's
own decoder rejected what it wrote.

The loop read "no content, no call" and did what it does for a silent round: nudged and sent the
same request again. Replaying the first failing prompt against the live provider, four times
each:

    as sent                              4 of 4 malformed
    + the empty-round directive          4 of 4 malformed   <- what the loop did
    temperature 0.7                      4 of 4 malformed
    tool_choice "required"               0 of 4             <- each one the call it meant

So the round is asked for once more with the call constrained to the schemas, and nothing is
added to the conversation to do it.
"""

from __future__ import annotations

from pathlib import Path

from kith import tools
from kith.config import default_config
from kith.domain.chat import Config
from kith.services import agent_loop


def _malformed() -> dict:
    """What the round looks like once the stream is parsed: nothing, and the reason why."""
    return {
        "content": "",
        "tool_calls": [],
        "stats": {
            "finishReason": "error",
            "nativeFinishReason": agent_loop._MALFORMED_CALL,
            "promptTokens": 0,
            "responseTokens": 0,
        },
    }


def _call() -> dict:
    return {
        "content": "",
        "tool_calls": [{"id": "c1", "function": {"name": "read_file", "arguments": '{"paths": ["x"]}'}}],
        "stats": {"finishReason": "tool_calls", "nativeFinishReason": "STOP"},
    }


def _spoke(text: str = "done") -> dict:
    return {"content": text, "tool_calls": [], "stats": {"finishReason": "stop"}}


def _script(*responses: dict):
    """Canned rounds, recording what each request asked for. The last one repeats."""
    sent: list[dict] = []

    def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
        sent.append({"tool_choice": tool_choice, "convo": [dict(m) for m in convo]})
        yield dict(responses[min(len(sent) - 1, len(responses) - 1)], type="turn")

    return fake, sent


def _run(fake, monkeypatch, db: Path, max_rounds: int = 8) -> list[dict]:
    monkeypatch.setattr(agent_loop, "_stream_once", fake)
    monkeypatch.setattr(agent_loop.time, "sleep", lambda _s: None)
    return list(
        agent_loop._run_turn(
            [{"role": "user", "content": "merge it"}],
            default_config(),
            "host",
            db,
            tools.host(db, language_server=False),
            max_rounds=max_rounds,
        )
    )


class TestTheNextRequest:
    def test_it_must_call_a_tool(self, db: Path, monkeypatch):
        fake, sent = _script(_malformed(), _call(), _spoke())
        _run(fake, monkeypatch, db)

        assert sent[0]["tool_choice"] == "auto"
        assert sent[1]["tool_choice"] == "required", "the retry is what makes the call parseable"

    def test_nothing_is_said_to_him_to_do_it(self, db: Path, monkeypatch):
        """The directive is the recovery that measured 4 of 4 malformed. The retry is the same
        conversation with a different flag, not a longer one."""
        fake, sent = _script(_malformed(), _call(), _spoke())
        events = _run(fake, monkeypatch, db)

        assert sent[1]["convo"] == sent[0]["convo"]
        assert not [e for e in events if e["type"] == "directive"]

    def test_only_that_one_request_is_constrained(self, db: Path, monkeypatch):
        """A round held to "required" cannot answer in prose, so the one after it is free again —
        otherwise the turn could never end."""
        fake, sent = _script(_malformed(), _call(), _spoke())
        events = _run(fake, monkeypatch, db)

        assert [s["tool_choice"] for s in sent] == ["auto", "required", "auto"]
        assert not [e for e in events if e["type"] == "error"]


class TestWhenItHappensAgain:
    def test_the_constrained_retry_is_spent_once(self, db: Path, monkeypatch):
        """If the constrained round comes back malformed too, it is an ordinary empty round from
        there: one nudge, then the turn says so. Not a loop of free retries."""
        fake, sent = _script(_malformed())
        events = _run(fake, monkeypatch, db)

        assert [s["tool_choice"] for s in sent] == ["auto", "required", "auto", "required"]
        assert len([e for e in events if e["type"] == "error"]) == 1

    def test_the_error_says_what_the_provider_said(self, db: Path, monkeypatch):
        """The first report of this read "all reasoning", which sent the diagnosis to the wrong
        side of the wire."""
        fake, _sent = _script(_malformed())
        events = _run(fake, monkeypatch, db)

        message = next(e for e in events if e["type"] == "error")["message"]
        assert agent_loop._MALFORMED_CALL in message
        assert "all reasoning" not in message


class TestWhatIsNotAffected:
    def test_a_reasoning_only_round_is_still_nudged(self, db: Path, monkeypatch):
        """The case the empty-round directive was built for. Nothing here says it was a call."""
        silent = {"content": "", "tool_calls": [], "stats": {"finishReason": "stop", "reasoningTokens": 900}}
        fake, sent = _script(silent, _spoke())
        events = _run(fake, monkeypatch, db)

        assert [s["tool_choice"] for s in sent] == ["auto", "auto"]
        assert [e["text"] for e in events if e["type"] == "directive"] == [agent_loop._EMPTY_ROUND_DIRECTIVE]


class TestTheTransportCarriesIt:
    def test_the_native_reason_is_read_off_the_stream(self, monkeypatch):
        """The only place the reason exists. The normalised one is "error", with no error object."""
        from kith.llm import openai_compat

        class FakeResp:
            status_code = 200
            encoding = "utf-8"
            text = ""

            def iter_lines(self, decode_unicode=True):
                return iter(
                    [
                        'data: {"id":"gen-1","choices":[{"delta":{"reasoning":"**Executing Git'
                        ' Commands**"},"finish_reason":null,"native_finish_reason":null}]}',
                        'data: {"id":"gen-1","choices":[{"delta":{"content":""},"finish_reason":'
                        '"error","native_finish_reason":"MALFORMED_FUNCTION_CALL"}],'
                        '"usage":{"prompt_tokens":0,"completion_tokens":0,"cost":0}}',
                        "data: [DONE]",
                    ]
                )

            def close(self):
                pass

        monkeypatch.setattr(openai_compat.requests, "post", lambda *a, **k: FakeResp())
        config = Config(
            model="m", num_ctx=0, num_predict=0, system="", think=False, base_url="x", api_key="k"
        )

        events = list(openai_compat.stream_once([{"role": "user", "content": "hi"}], config))

        assert not [e for e in events if e["type"] == "error"], "no error object, so not a failed request"
        turn = next(e for e in events if e["type"] == "turn")
        assert turn["tool_calls"] == [] and turn["content"] == ""
        assert turn["stats"]["finishReason"] == "error"
        assert turn["stats"]["nativeFinishReason"] == "MALFORMED_FUNCTION_CALL"
