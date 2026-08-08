"""A provider that drops one round must not cost the whole turn.

The failure this is about, verbatim off a real turn:

    Could not reach the cloud model at https://openrouter.ai/api/v1/chat/completions:
    ('Connection aborted.', TimeoutError('The write operation timed out'))

Round six. The five before it had read a skill, filed a task, written two notes, run twenty
commands, searched eight times and touched fifteen files — 42,459 tokens of finished work —
and all of it went, because the loop's answer to an error was `yield event; return`. The
client's own retry does not help and must not: it only ever retries *starting* a turn, and
stops the moment a response body exists, because by then he may have written files and
committed.

Retrying the round is safe in a way retrying the turn is not. The tools have already run and
their results are already in `convo`; only the model call repeats. What is not safe is
retrying a round that already emitted — the second attempt may say something different, and
the person would watch half of one answer followed by all of another. So that case lands
instead: he keeps his rounds and spends the reserve writing down what they found.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith.config import default_config
from kith.services import agent_loop, tuning


def _errors(*messages: str):
    """Streams that fail, then one that succeeds — one canned response per call."""
    responses = [[{"type": "error", "message": m}] for m in messages]
    responses.append([{"type": "turn", "content": "done", "tool_calls": [], "stats": {}}])
    calls: list[int] = []

    def fake(convo, config, host, tools=None, tool_choice="auto"):
        calls.append(1)
        yield from responses[min(len(calls) - 1, len(responses) - 1)]

    return fake, calls


class TestARetryableFailureIsRetried:
    def test_a_dropped_connection_is_tried_again(self, db: Path, monkeypatch):
        fake, calls = _errors("Could not reach the cloud model at https://x: Connection aborted.")
        monkeypatch.setattr(agent_loop, "_stream_once", fake)
        monkeypatch.setattr(agent_loop.time, "sleep", lambda _s: None)

        events = list(agent_loop._run_turn([], default_config(), "host", db, max_rounds=3))

        assert len(calls) == 2, "the round should have been attempted twice"
        assert not [e for e in events if e["type"] == "error"], "a retried round is not an error"

    @pytest.mark.parametrize(
        "message",
        [
            "Could not reach the cloud model at https://x: Connection aborted.",
            "Cloud model returned 502: upstream unavailable",
            "Cloud model returned 503: no instances",
            "Cloud model returned 429: rate limited",
            "Could not reach the cloud model at https://x: Read timed out.",
        ],
    )
    def test_the_shapes_worth_retrying(self, db: Path, monkeypatch, message):
        fake, calls = _errors(message)
        monkeypatch.setattr(agent_loop, "_stream_once", fake)
        monkeypatch.setattr(agent_loop.time, "sleep", lambda _s: None)

        list(agent_loop._run_turn([], default_config(), "host", db, max_rounds=3))

        assert len(calls) == 2, f"{message!r} should have been retried"

    def test_it_backs_off_between_attempts(self, db: Path, monkeypatch):
        """Hammering an upstream that just refused is how a rate-limit becomes a ban."""
        waits: list[float] = []
        fake, _ = _errors("Cloud model returned 502: upstream unavailable", "Cloud model returned 502: again")
        monkeypatch.setattr(agent_loop, "_stream_once", fake)
        monkeypatch.setattr(agent_loop.time, "sleep", lambda s: waits.append(s))

        list(agent_loop._run_turn([], default_config(), "host", db, max_rounds=3))

        assert waits == sorted(waits) and len(waits) >= 2, f"should back off, waited {waits}"
        assert waits[1] > waits[0], "each wait should be longer than the last"


class TestWhatIsNotWorthRetrying:
    @pytest.mark.parametrize(
        "message",
        [
            "Cloud model returned 400: messages[3] is malformed",
            "Cloud model returned 401: no credit",
        ],
    )
    def test_a_request_the_server_calls_wrong_is_not_sent_again(self, db: Path, monkeypatch, message):
        """It will be just as wrong the second time, only slower.

        "Not sent again" means *this round* is not repeated — not that the turn stops. It still
        lands, which is a different request: a smaller toolset and a directive asking him to
        write down what he has. That call may well succeed where the failed one could not, and
        it is the difference between losing the turn's work and keeping it.
        """
        sent: list[str] = []

        def fake(convo, config, host, tools=None, tool_choice="auto"):
            sent.append(str((convo[-1] if convo else {}).get("content") or ""))
            if len(sent) == 1:
                yield {"type": "error", "message": message}
            else:
                yield {"type": "turn", "content": "wrote it down", "tool_calls": [], "stats": {}}

        monkeypatch.setattr(agent_loop, "_stream_once", fake)
        monkeypatch.setattr(agent_loop.time, "sleep", lambda _s: None)

        list(agent_loop._run_turn([], default_config(), "host", db, max_rounds=3))

        repeats = [one for one in sent[1:] if one == sent[0]]
        assert not repeats, f"{message!r} was re-sent unchanged: {sent}"
        if len(sent) > 1:
            assert agent_loop._LANDING_DIRECTIVE in sent[1], "the second call should be the landing one"

    def test_a_round_that_already_spoke_is_not_replayed(self, db: Path, monkeypatch):
        """The second attempt may answer differently, and the person would watch half of one
        answer followed by all of another. That round is over; land instead."""
        calls: list[int] = []

        def fake(convo, config, host, tools=None, tool_choice="auto"):
            calls.append(1)
            if len(calls) == 1:
                yield {"type": "delta", "role": "text", "text": "I had a look and "}
                yield {"type": "error", "message": "Cloud model returned 502: upstream unavailable"}
            else:
                yield {"type": "turn", "content": "done", "tool_calls": [], "stats": {}}

        monkeypatch.setattr(agent_loop, "_stream_once", fake)
        monkeypatch.setattr(agent_loop.time, "sleep", lambda _s: None)

        list(agent_loop._run_turn([], default_config(), "host", db, max_rounds=6))

        assert calls, "the round ran"
        # It may go on to land, but it must not have re-sent the same round.
        assert len(calls) <= 2


class TestGivingUpMeansLandingNotDying:
    def test_an_exhausted_retry_does_not_end_the_turn(self, db: Path, monkeypatch):
        """The whole point. Five rounds of work must survive the sixth round failing."""
        tuning.apply({"landing_reserve": 2})
        calls: list[int] = []

        def always_fails(convo, config, host, tools=None, tool_choice="auto"):
            calls.append(1)
            if len(calls) <= 3:
                yield {"type": "error", "message": "Cloud model returned 502: upstream unavailable"}
            else:
                yield {"type": "turn", "content": "wrote it down", "tool_calls": [], "stats": {}}

        monkeypatch.setattr(agent_loop, "_stream_once", always_fails)
        monkeypatch.setattr(agent_loop.time, "sleep", lambda _s: None)

        events = list(agent_loop._run_turn([], default_config(), "host", db, max_rounds=8))

        assert len(calls) > 3, "it gave up entirely instead of landing"
        assert not [e for e in events if e["type"] == "error"], (
            "a turn that recovered by landing is not an errored turn"
        )

    def test_failing_again_while_landing_does_end_it(self, db: Path, monkeypatch):
        """One recovery attempt. A provider that is still refusing during the landing round is
        not going to be talked round, and a loop that never gives up is worse than an error."""

        def always_fails(convo, config, host, tools=None, tool_choice="auto"):
            yield {"type": "error", "message": "Cloud model returned 502: upstream unavailable"}

        monkeypatch.setattr(agent_loop, "_stream_once", always_fails)
        monkeypatch.setattr(agent_loop.time, "sleep", lambda _s: None)

        events = list(agent_loop._run_turn([], default_config(), "host", db, max_rounds=8))

        assert [e for e in events if e["type"] == "error"], "it must surface the failure eventually"
