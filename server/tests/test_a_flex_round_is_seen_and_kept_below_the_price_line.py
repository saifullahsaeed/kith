"""A cheaper service tier is visible when it serves a round, and the conversation stays cheap.

One Sadeef One chat on 2026-10-01 ran `openai/gpt-6.1-sol-pro` pinned to `openai/flex`, and every
round was served on flex — OpenRouter's own generation record says so — yet it billed like the
standard tier. Each part of why is a test here:

* Nothing in Kith said which tier served a round, so a pin that held looked like one that had not.
* The meter clamped Sol Pro's real 1.4 characters a token to 2.0 and read a 349k request as 237k,
  so nothing folded before 272k, the line past which OpenAI doubles every rate.
* The same pin was sent for Gemini, Opus and Mimo, which that host does not serve, and switched
  off the host ranking for all three.
* A test run the turn had already read woke the chat two minutes later, to re-send everything
  and say "already included in my last update" for $0.25.
"""

from __future__ import annotations

import time

import pytest

from kith.domain import endpoints
from kith.domain.chat import Config, Routing
from kith.engine.run import processes as process_service
from kith.kernel import session_context
from kith.llm import budget, openai_compat
from kith.services import routing


def _config(model: str = "openai/gpt-6.1-sol-pro") -> Config:
    return Config(
        model=model,
        num_ctx=0,
        num_predict=0,
        system="",
        think=False,
        base_url="https://openrouter.ai/api/v1",
        api_key="k",
    )


class TestTheTierIsRecorded:
    def test_the_tier_a_round_was_served_on_is_in_its_stats(self, monkeypatch):
        class FakeResp:
            status_code = 200
            encoding = "utf-8"
            text = ""

            def iter_lines(self, decode_unicode=True):
                return iter(
                    [
                        'data: {"id":"gen-1","provider":"OpenAI","service_tier":"flex",'
                        '"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}',
                        'data: {"id":"gen-1","provider":"OpenAI","service_tier":"flex",'
                        '"choices":[{"delta":{"content":""},"finish_reason":"stop"}],'
                        '"usage":{"prompt_tokens":9,"completion_tokens":5,"cost":0.000034}}',
                        "data: [DONE]",
                    ]
                )

            def close(self):
                pass

        monkeypatch.setattr(openai_compat.requests, "post", lambda *a, **k: FakeResp())
        events = list(openai_compat.stream_once([{"role": "user", "content": "hi"}], _config()))
        stats = next(e for e in events if e["type"] == "turn")["stats"]
        assert stats["provider"] == "OpenAI"
        assert stats["serviceTier"] == "flex"

    def test_a_host_that_does_not_say_leaves_it_blank(self):
        assert openai_compat._stats({}, 1.0, "m", "Novita")["serviceTier"] == ""


class TestTheMeterBelievesAModelThatBillsMoreTokens:
    def test_sol_pros_ratio_is_used_rather_than_clamped(self):
        """The request billed 348,688 tokens for about 475,000 characters."""
        room = budget.ContextBudget(window=1_050_000)
        room.observe(prompt_tokens=348_688, chars=475_000)
        assert room.chars_per_token == pytest.approx(475_000 / 348_688)
        assert room.projected(475_000) == 348_688


def _host(tag: str, name: str) -> endpoints.Endpoint:
    return endpoints.Endpoint(slug=tag, name=name)


SOL_HOSTS = [_host("openai/flex", "OpenAI"), _host("openai", "OpenAI"), _host("azure", "Azure")]
GEMINI_HOSTS = [_host("google-ai-studio", "Google AI Studio"), _host("google-vertex", "Google")]


class TestAPinIsForTheModelsItsHostServes:
    @pytest.mark.parametrize("pin", ["openai/flex", "openai", "OpenAI", "Azure"])
    def test_every_spelling_the_router_takes_is_recognised(self, pin):
        assert endpoints.serves(SOL_HOSTS, pin)

    def test_a_host_this_model_does_not_have_is_not(self):
        assert not endpoints.serves(GEMINI_HOSTS, "openai/flex")

    def test_the_pin_is_kept_for_a_model_that_host_serves(self, monkeypatch):
        monkeypatch.setattr(routing, "_rows", lambda config: SOL_HOSTS)
        routing.forget()
        kept = routing._for_this_model(Routing(pinned="openai/flex"), _config())
        assert kept.pinned == "openai/flex"

    def test_it_is_dropped_for_a_model_that_host_does_not_serve(self, monkeypatch):
        """So the host ranking runs for Gemini instead of an order the router ignores."""
        monkeypatch.setattr(routing, "_rows", lambda config: GEMINI_HOSTS)
        routing.forget()
        dropped = routing._for_this_model(Routing(pinned="openai/flex"), _config("google/gemini-3.8-flash"))
        assert dropped.pinned == ""
        assert routing._rankable(dropped, _config("google/gemini-3.8-flash"))

    def test_it_is_kept_when_the_hosts_cannot_be_read(self, monkeypatch):
        """Not knowing is not evidence the pin is wrong."""
        monkeypatch.setattr(routing, "_rows", lambda config: [])
        routing.forget()
        kept = routing._for_this_model(Routing(pinned="openai/flex"), _config("google/gemini-3.8-flash"))
        assert kept.pinned == "openai/flex"


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setattr(process_service, "_LOG_DIR", tmp_path / "processes", raising=False)
    process_service.processes._running.clear()
    yield process_service.processes
    process_service.processes._running.clear()


def _finished(registry, name: str) -> None:
    with session_context.working_in("c-1"):
        registry.start("echo 160 passed", name)
    for _ in range(300):
        if not registry._find(name, "c-1").running:
            return
        time.sleep(0.01)
    raise AssertionError(f"{name} never exited")


class TestAFinishedTaskTheTurnHasReadDoesNotWakeIt:
    def test_reading_it_finished_is_being_told(self, registry):
        _finished(registry, "backend-tests")
        seen = registry.check("backend-tests", "c-1")
        assert seen["alive"] is False and "160 passed" in seen["output"]
        assert process_service.finished_since_last_look() == {}

    def test_one_nobody_has_read_still_wakes_its_chat(self, registry):
        _finished(registry, "unread")
        assert "c-1" in process_service.finished_since_last_look()

    def test_reading_it_while_it_runs_does_not_count(self, registry):
        with session_context.working_in("c-1"):
            registry.start("sleep 1 && echo done", "slow")
        assert registry.check("slow", "c-1")["alive"] is True
        for _ in range(500):
            if not registry._find("slow", "c-1").running:
                break
            time.sleep(0.01)
        assert "c-1" in process_service.finished_since_last_look()
