"""A pinned host is a lock until the round's last attempt, then a preference.

2026-10-01: a conversation pinned to `openai/flex` built a 1.25M-token turn at 81% cached, and
the one-word reply after it went to Azure — the pin was sent with `allow_fallbacks: True`, so
the first time flex was busy OpenRouter moved the round to a host holding none of the cache and
no flex rate. 83k prompt tokens, 81% billed fresh, for "ok good enough".

A busy flex host is usually busy for seconds. So the round now waits for it — every attempt but
the last insists on the pin — and only the last lets fallbacks in, so a pin that stays down
still costs a cold cache and never a dead turn.
"""

from __future__ import annotations

from kith.domain.chat import Config, Routing
from kith.llm.openai_compat import _routing_options
from kith.services import agent_loop


def _config() -> Config:
    return Config(model="openai/gpt-6.1-sol", num_ctx=0, num_predict=0, system="", think=False)


def _sender(*outcomes: list[dict]):
    sent: list[Routing | None] = []

    def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
        sent.append(routing)
        yield from outcomes[min(len(sent) - 1, len(outcomes) - 1)]

    return fake, sent


def _run(monkeypatch, routing, *outcomes):
    fake, sent = _sender(*outcomes)
    monkeypatch.setattr(agent_loop, "_stream_once", fake)
    monkeypatch.setattr(agent_loop.time, "sleep", lambda _s: None)
    gen = agent_loop._send_round([], _config(), "host", [], agent_loop._Retries(), routing=routing)
    try:
        while True:
            next(gen)
    except StopIteration as done:
        return done.value, sent


OK = [{"type": "turn", "content": "done", "tool_calls": [], "stats": {}}]
BUSY = [{"type": "error", "message": "Cloud model returned 429: flex capacity"}]
NO_ENDPOINT = [{"type": "error", "message": "Cloud model returned 404: No endpoints found"}]


class TestThePayload:
    def test_a_strict_pin_turns_fallbacks_off(self):
        provider = _routing_options(_config(), Routing(pinned="openai/flex", strict=True))["provider"]
        assert provider["order"] == ["openai/flex"]
        assert provider["allow_fallbacks"] is False

    def test_a_released_pin_lets_them_back_in(self):
        provider = _routing_options(_config(), Routing(pinned="openai/flex"))["provider"]
        assert provider["allow_fallbacks"] is True


class TestTheRound:
    def test_a_round_that_gets_through_was_sent_locked(self, monkeypatch):
        (_, _, _, failure, _), sent = _run(monkeypatch, Routing(pinned="openai/flex"), OK)
        assert failure is None
        assert [r.strict for r in sent] == [True]

    def test_a_busy_pin_is_waited_for_then_released_on_the_last_attempt(self, monkeypatch):
        (_, _, _, failure, _), sent = _run(monkeypatch, Routing(pinned="openai/flex"), BUSY, BUSY, OK)
        assert failure is None
        assert [r.strict for r in sent] == [True, True, False]

    def test_no_endpoint_under_the_lock_still_earns_the_released_attempt(self, monkeypatch):
        # A 404 is not worth repeating as-is, but under a lock it may only be the lock talking.
        (_, _, _, failure, _), sent = _run(
            monkeypatch, Routing(pinned="openai/flex"), NO_ENDPOINT, NO_ENDPOINT, OK
        )
        assert failure is None
        assert [r.strict for r in sent] == [True, True, False]

    def test_nothing_pinned_is_never_locked(self, monkeypatch):
        (_, _, _, failure, _), sent = _run(monkeypatch, Routing(), NO_ENDPOINT)
        assert failure is not None
        assert len(sent) == 1 and sent[0].strict is False
