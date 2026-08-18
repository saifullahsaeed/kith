"""Searching with a model that is not allowed to stop thinking.

The web search sends `reasoning: {enabled: false}`, because it throws the completion away
and has no use for a thought. Some models refuse: "Reasoning is mandatory for this endpoint
and cannot be disabled", HTTP 400 — `google/gemini-3.7-flash` among them, which is what he
was thinking with. So every search failed on the switch alone, and what he saw was the
"search returned nothing / this is a plumbing failure" note, once per attempt, with nothing
in it to suggest the request had never reached a search engine.

The transport had handled this refusal for a long time (`llm/openai_compat`). This file had
its own copy of the payload and no copy of the lesson, which is the shape worth a test: the
two places that speak the chat API must agree about the ways it says no.
"""

from __future__ import annotations

import json

import pytest

from kith.domain import connection
from kith.domain.chat import Config
from kith.infra import websearch

REFUSAL = json.dumps(
    {"error": {"message": "Reasoning is mandatory for this endpoint and cannot be disabled.", "code": 400}}
)


def _config() -> Config:
    return Config(
        model="google/gemini-3.7-flash",
        num_ctx=0,
        num_predict=0,
        system="",
        think=True,
        base_url="https://openrouter.ai/api/v1",
        api_key="sk-or-test",
    )


class _Response:
    """Just enough of `requests.Response` for the paths under test."""

    def __init__(self, status_code: int, body: str = "", payload: dict | None = None):
        self.status_code = status_code
        self.text = body
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


def _hit_body() -> dict:
    return {
        "choices": [
            {
                "message": {
                    "annotations": [
                        {
                            "type": "url_citation",
                            "url_citation": {
                                "title": "MTEB Leaderboard",
                                "url": "https://huggingface.co/spaces/mteb/leaderboard",
                                "content": "The leaderboard ranks embedding models.",
                            },
                        }
                    ]
                }
            }
        ]
    }


@pytest.fixture
def sent(monkeypatch):
    """Record every request the search makes, and reply from a scripted queue."""
    calls: list[dict] = []
    replies: list[_Response] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        # A copy, because the retry drops a key from the same dict — recording the live one
        # would show the first request as if it had never carried the switch.
        calls.append(dict(json or {}))
        return replies.pop(0)

    monkeypatch.setattr(websearch.requests, "post", fake_post)
    return calls, replies


class TestARefusedSwitchIsDroppedRatherThanFatal:
    def test_the_search_is_sent_again_without_it_and_still_returns_hits(self, sent):
        calls, replies = sent
        replies.extend([_Response(400, REFUSAL), _Response(200, payload=_hit_body())])

        hits = websearch._openrouter("mteb arabic", 5, _config())

        assert [h["url"] for h in hits] == ["https://huggingface.co/spaces/mteb/leaderboard"]
        assert len(calls) == 2
        assert "reasoning" in calls[0]
        assert "reasoning" not in calls[1]

    def test_the_retry_changes_nothing_else(self, sent):
        """Only the refused key comes out. The plugin, the carrier and the tiny output
        ceiling all still have to be there — the ceiling especially, since it is what keeps a
        model that must think from being billed for thinking about an answer we discard."""
        calls, replies = sent
        replies.extend([_Response(400, REFUSAL), _Response(200, payload=_hit_body())])

        websearch._openrouter("mteb arabic", 5, _config())

        first, retry = calls
        assert retry == {k: v for k, v in first.items() if k != "reasoning"}
        assert retry["max_tokens"] == 16
        assert retry["plugins"][0]["id"] == "web"


class TestEveryOtherFailureIsStillAFailure:
    def test_an_unrelated_400_is_not_sent_twice(self, sent):
        """A genuine bad request must surface as one. Retrying every 400 would hide the
        cause and bill for two searches instead of one."""
        calls, replies = sent
        replies.append(_Response(400, json.dumps({"error": {"message": "Invalid model id"}})))

        with pytest.raises(RuntimeError, match="400"):
            websearch._openrouter("mteb arabic", 5, _config())

        assert len(calls) == 1

    def test_a_refusal_twice_over_is_reported_not_looped(self, sent):
        calls, replies = sent
        replies.extend([_Response(400, REFUSAL), _Response(400, REFUSAL)])

        with pytest.raises(RuntimeError, match="Reasoning is mandatory"):
            websearch._openrouter("mteb arabic", 5, _config())

        assert len(calls) == 2


class TestAModelThatAllowsItIsStillAskedOnlyOnce:
    def test_no_second_request_when_the_switch_is_accepted(self, sent):
        calls, replies = sent
        replies.append(_Response(200, payload=_hit_body()))

        assert websearch._openrouter("mteb arabic", 5, _config())
        assert len(calls) == 1
        assert calls[0]["reasoning"] == {"enabled": False}


class TestTheClassifierIsSharedRatherThanCopied:
    """It lives in `domain/connection.py` because both callers need it and `infra` may not
    import `llm` — the same move `is_openrouter` made, for the same reason."""

    def test_it_reads_the_providers_words(self):
        assert connection.refuses_reasoning(REFUSAL)
        assert connection.refuses_reasoning("REASONING cannot be disabled")

    def test_an_unrelated_body_is_not_a_refusal(self):
        assert not connection.refuses_reasoning("Invalid model id")
        assert not connection.refuses_reasoning("")

    def test_the_transport_asks_it_the_same_question(self):
        from kith.llm import openai_compat

        assert openai_compat._refuses_reasoning(_Response(400, REFUSAL))
        assert not openai_compat._refuses_reasoning(_Response(400, "Invalid model id"))
