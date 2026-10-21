"""A model request sent before the Wi-Fi is back waits for it, instead of ending the turn.

Waking from sleep, the next request fails on name resolution in hundredths of a second; the round's
three quick attempts were spent long before a Mac rejoins its network, and the turn ended on "Could
not reach the cloud model" (twice on 2026-10-15, 38 and 56 seconds after a wake). Only a failure that
never reached anyone is waited out — a dropped connection after the request went out is not.
"""

from __future__ import annotations

import pytest
import requests

from kith.kernel import session_context, stopping
from kith.llm import openai_compat

_NO_DNS = "HTTPSConnectionPool(host='openrouter.ai', port=443): NameResolutionError: Failed to resolve 'openrouter.ai'"


@pytest.fixture
def slept(monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr(openai_compat.time, "sleep", waits.append)
    return waits


def _failing(times: int, message: str):
    calls = {"n": 0}

    def post(url, **kwargs):
        calls["n"] += 1
        if calls["n"] <= times:
            raise requests.exceptions.ConnectionError(message)
        return "the response"

    return post, calls


def test_no_network_is_waited_out(monkeypatch, slept):
    post, calls = _failing(3, _NO_DNS)
    monkeypatch.setattr(openai_compat.requests, "post", post)

    assert openai_compat._post("https://openrouter.ai/api/v1/chat/completions", {}, {}) == "the response"
    assert calls["n"] == 4
    assert slept == [1.0, 2.0, 4.0]


def test_a_dropped_connection_is_not_waited_out(monkeypatch, slept):
    """After the request went out, the provider may already be answering it. That is the round
    retry's decision, not the transport's."""
    post, calls = _failing(1, "('Connection aborted.', RemoteDisconnected('closed'))")
    monkeypatch.setattr(openai_compat.requests, "post", post)

    with pytest.raises(requests.exceptions.ConnectionError):
        openai_compat._post("https://openrouter.ai/api/v1/chat/completions", {}, {})
    assert calls["n"] == 1
    assert slept == []


def test_it_gives_up_in_the_end(monkeypatch, slept):
    post, _ = _failing(10_000, _NO_DNS)
    monkeypatch.setattr(openai_compat.requests, "post", post)
    clock = {"now": 0.0}
    monkeypatch.setattr(openai_compat.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(openai_compat.time, "sleep", lambda s: clock.__setitem__("now", clock["now"] + s))

    with pytest.raises(requests.exceptions.ConnectionError):
        openai_compat._post("https://openrouter.ai/api/v1/chat/completions", {}, {})
    assert clock["now"] <= openai_compat._NETWORK_PATIENCE_SECONDS


def test_a_stopped_turn_stops_waiting(monkeypatch, slept):
    post, calls = _failing(10, _NO_DNS)
    monkeypatch.setattr(openai_compat.requests, "post", post)
    switch = stopping.arm("c-stop")
    switch.set()
    try:
        with session_context.working_in("c-stop"), pytest.raises(requests.exceptions.ConnectionError):
            openai_compat._post("https://openrouter.ai/api/v1/chat/completions", {}, {})
    finally:
        stopping.disarm("c-stop", switch)
    assert calls["n"] == 1
