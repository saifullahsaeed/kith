"""A streamed answer has to actually stream.

Reported as "from my message to the AI responding it takes around 10 sec, but when it starts
responding it's instant" — which is not a thing a language model does. It was this module: the
response body was read in full before the loop that iterates it ever ran, so every token was
downloaded, discarded, and then replayed at once.

The read was `_body(response)`, which is `response.text`, called unconditionally on the way to
`budget.looks_like_overflow` — a function whose first line returns False unless the status is
400. So on every successful request the entire answer was fetched to answer a question that had
already been answered by the status code.

Measured against a provider stub emitting eighteen tokens 150ms apart: before, all eighteen
arrived in the same millisecond, 5.63s in. After, the first at 3.01s and the rest on the stub's
own cadence.

These tests are about *when* bytes are touched, not what comes out of them, because that is
where the bug lived. A test that only checked the events would have passed throughout.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from kith.config import default_config
from kith.llm import openai_compat


def _chunk(**delta) -> str:
    return "data: " + json.dumps({"choices": [{"index": 0, "delta": delta}]})


class _Response:
    """A streamed response that records every attempt to read it whole."""

    def __init__(self, status: int = 200, lines: list[str] | None = None, body: str = ""):
        self.status_code = status
        self.encoding = "utf-8"
        self.headers: dict[str, str] = {}
        self._lines = lines or []
        self._body = body
        #: The count that matters. Reading `.text` on a streamed response consumes it.
        self.whole_body_reads = 0
        self.closed = False

    @property
    def text(self) -> str:
        self.whole_body_reads += 1
        return self._body

    def iter_lines(self, decode_unicode: bool = False):
        yield from self._lines

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def cloud():
    return replace(default_config(), base_url="https://provider.test/v1", api_key="sk-test")


def _served(monkeypatch, response: _Response) -> None:
    monkeypatch.setattr(openai_compat.requests, "post", lambda *a, **k: response)


class TestTheBodyIsNotSwallowedBeforeItIsStreamed:
    def test_a_successful_response_is_never_read_whole(self, cloud, monkeypatch):
        """The whole bug, in one assertion.

        `.text` on a streamed response downloads all of it. Doing that before iterating is
        what turned a stream into a single lump, and nothing about the events it produced
        looked any different afterwards — which is why it survived so long.
        """
        response = _Response(lines=[_chunk(content="hello "), _chunk(content="there"), "data: [DONE]"])
        _served(monkeypatch, response)

        events = list(openai_compat.stream_once([{"role": "user", "content": "hi"}], cloud, "host"))

        assert response.whole_body_reads == 0, "the answer was downloaded before it was streamed"
        assert [e["text"] for e in events if e["type"] == "delta"] == ["hello ", "there"]

    def test_tokens_leave_as_they_arrive(self, cloud, monkeypatch):
        """Not just *that* they come out, but that none waits for the last one.

        The generator is consumed one item at a time and the upstream records how far it has
        been read at each step. If anything buffers, the upstream is exhausted before the
        first delta is handed over.
        """
        reached: list[int] = []

        class _Slow(_Response):
            def iter_lines(self, decode_unicode: bool = False):
                for i, line in enumerate(self._lines):
                    reached.append(i)
                    yield line

        response = _Slow(lines=[_chunk(content=str(i)) for i in range(5)] + ["data: [DONE]"])
        _served(monkeypatch, response)

        stream = openai_compat.stream_once([{"role": "user", "content": "hi"}], cloud, "host")
        first = next(event for event in stream if event["type"] == "delta")

        assert first["text"] == "0"
        assert len(reached) < 6, f"the whole upstream was read before the first token: {reached}"


class TestAFailureIsStillClassified:
    """The read was there for a reason, and the reason has to survive the fix."""

    def test_an_overflow_is_still_recognised(self, cloud, monkeypatch):
        response = _Response(status=400, body="maximum context length is 128000 tokens")
        _served(monkeypatch, response)

        events = list(openai_compat.stream_once([{"role": "user", "content": "hi"}], cloud, "host"))

        assert events and events[0]["type"] == "error"
        assert events[0].get("kind") == "context_overflow", events[0]
        assert response.whole_body_reads >= 1, "a failure body still has to be read"

    def test_an_ordinary_failure_still_carries_what_the_provider_said(self, cloud, monkeypatch):
        response = _Response(status=502, body="upstream unavailable")
        _served(monkeypatch, response)

        events = list(openai_compat.stream_once([{"role": "user", "content": "hi"}], cloud, "host"))

        assert events and events[0]["type"] == "error"
        assert "502" in events[0]["message"] and "upstream unavailable" in events[0]["message"]
