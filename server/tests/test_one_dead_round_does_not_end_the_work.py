"""One round dying is not the same as the turn being finished.

The loop's answer to a round that had failed all three of its attempts was `landing`, and
landing is a one-way door: it cuts the toolset to the nineteen finishing tools and appends a
directive that opens "You're near the end of this turn's tool budget, so stop gathering".

Measured on turn 1244, 2026-08-12. Round 2 of a 40-round budget. One round died, the loop
landed, and the next request went out with `built_in_tools` down from 10,601 tokens to 3,392 —
no `grep`, no `shell`, no `read_file`-and-then-edit-it — and a sentence telling him he was
nearly out of budget. He believed it, wrote a seven-point plan of the work he was about to do,
called nothing, and stopped. His person replied "what are you waiting for then man"; the next
turn, with the toolset back, did the whole thing in sixteen rounds and committed it.

So the recovery now depends on what the failure actually says:

* a transport failure or a 5xx says the provider is unwell and nothing about the request — the
  round is noted and the turn carries on, whole;
* a 400 or a 401 says the request itself is wrong or unpaid, and there the smaller request
  landing makes is the point rather than the cost;
* two dead rounds in a row is an outage, and landing is right again.
"""

from __future__ import annotations

from pathlib import Path

from kith import tools
from kith.config import default_config
from kith.services import agent_loop


def _tools_offered(schemas) -> set[str]:
    return {s["function"]["name"] for s in (schemas or [])}


#: A round dies only once all `_ROUND_ATTEMPTS` of it have failed — which is the case this file
#: is about, and the easiest one to write a fake that misses. A stream that fails once and then
#: succeeds never leaves the round at all: the second call is attempt 2 of the same request,
#: against the same unchanged `convo`, and asserting on it tells you nothing about what happens
#: after a round is actually lost.
_KILLS_A_ROUND = agent_loop._ROUND_ATTEMPTS


class TestATransientFailureKeepsTheTurnWhole:
    def test_the_next_round_still_has_every_tool(self, db: Path, monkeypatch):
        """The measured symptom: 69 schemas became 19 because one request timed out."""
        offered: list[set[str]] = []

        def flaky(convo, config, host, tools=None, tool_choice="auto", routing=None):
            offered.append(_tools_offered(tools))
            if len(offered) <= _KILLS_A_ROUND:
                yield {"type": "error", "message": "Cloud model returned 502: upstream unavailable"}
            else:
                yield {"type": "turn", "content": "carried on", "tool_calls": [], "stats": {}}

        monkeypatch.setattr(agent_loop, "_stream_once", flaky)
        monkeypatch.setattr(agent_loop.time, "sleep", lambda _s: None)

        list(
            agent_loop._run_turn(
                [], default_config(), "host", db, tools.host(db, language_server=False), max_rounds=40
            )
        )

        after = offered[_KILLS_A_ROUND:]
        assert after, "the turn should have gone again after the dead round"
        assert after[0] == offered[0], (
            "the round after a provider blip was offered a different toolset: "
            f"lost {sorted(offered[0] - after[0])}"
        )

    def test_he_is_not_told_he_is_near_the_end_of_his_budget(self, db: Path, monkeypatch):
        """He was on round 2 of 40. The directive is what he acted on, not the toolset."""
        sent: list[str] = []

        def flaky(convo, config, host, tools=None, tool_choice="auto", routing=None):
            sent.append(str((convo[-1] if convo else {}).get("content") or ""))
            if len(sent) <= _KILLS_A_ROUND:
                yield {
                    "type": "error",
                    "message": "Could not reach the cloud model at https://x: Read timed out.",
                }
            else:
                yield {"type": "turn", "content": "carried on", "tool_calls": [], "stats": {}}

        monkeypatch.setattr(agent_loop, "_stream_once", flaky)
        monkeypatch.setattr(agent_loop.time, "sleep", lambda _s: None)

        list(
            agent_loop._run_turn(
                [], default_config(), "host", db, tools.host(db, language_server=False), max_rounds=40
            )
        )

        after = sent[_KILLS_A_ROUND:]
        assert after, "the turn should have gone again after the dead round"
        assert agent_loop._LANDING_DIRECTIVE not in after[0], "one dead round is not a spent budget"
        assert agent_loop._ROUND_FAILED_DIRECTIVE in after[0], (
            "the dead round left no assistant message, so the gap has to be accounted for — "
            "otherwise the likeliest reading of the history is that he already answered"
        )

    def test_the_person_still_sees_that_it_happened(self, db: Path, monkeypatch):
        """Carrying on quietly would make a 30-second stall look like slow thinking."""
        calls: list[int] = []

        def flaky(convo, config, host, tools=None, tool_choice="auto", routing=None):
            calls.append(1)
            if len(calls) <= _KILLS_A_ROUND:
                yield {"type": "error", "message": "Cloud model returned 429: rate limited"}
            else:
                yield {"type": "turn", "content": "carried on", "tool_calls": [], "stats": {}}

        monkeypatch.setattr(agent_loop, "_stream_once", flaky)
        monkeypatch.setattr(agent_loop.time, "sleep", lambda _s: None)

        events = list(
            agent_loop._run_turn(
                [], default_config(), "host", db, tools.host(db, language_server=False), max_rounds=40
            )
        )

        assert [e for e in events if e["type"] == "retrying"], "the retry must still be visible"
        assert not [e for e in events if e["type"] == "error"], "a turn that carried on is not errored"


class TestAnOutageStillLands:
    def test_two_dead_rounds_in_a_row_hands_over_to_landing(self, db: Path, monkeypatch):
        """Absorbing the first is a blip. Absorbing every one is a loop with no way out."""
        sent: list[str] = []

        def dead(convo, config, host, tools=None, tool_choice="auto", routing=None):
            sent.append(str((convo[-1] if convo else {}).get("content") or ""))
            yield {"type": "error", "message": "Cloud model returned 502: upstream unavailable"}

        monkeypatch.setattr(agent_loop, "_stream_once", dead)
        monkeypatch.setattr(agent_loop.time, "sleep", lambda _s: None)

        events = list(
            agent_loop._run_turn(
                [], default_config(), "host", db, tools.host(db, language_server=False), max_rounds=40
            )
        )

        assert any(agent_loop._LANDING_DIRECTIVE in one for one in sent), "it never tried to land"
        assert [e for e in events if e["type"] == "error"], "it must surface the failure eventually"

    def test_a_round_that_comes_back_clears_the_count(self, db: Path, monkeypatch):
        """A blip on round 2 says nothing about round 25, so it must not be held against it."""
        sent: list[str] = []
        # Never two dead rounds in a row. A dead round is `_ROUND_ATTEMPTS` calls, not one, so
        # the script is walked a *round* at a time and the failing steps swallow their retries.
        script = ["fail", "work", "fail", "work", "fail", "stop"]
        step = iter(script)
        now = next(step)
        left = _KILLS_A_ROUND

        def intermittent(convo, config, host, tools=None, tool_choice="auto", routing=None):
            nonlocal now, left
            sent.append(str((convo[-1] if convo else {}).get("content") or ""))
            if now == "fail":
                left -= 1
                if left == 0:
                    now, left = next(step, "stop"), _KILLS_A_ROUND
                yield {"type": "error", "message": "Cloud model returned 503: no instances"}
                return
            if now == "work":
                now, left = next(step, "stop"), _KILLS_A_ROUND
                yield {
                    "type": "turn",
                    "content": "",
                    "tool_calls": [{"function": {"name": "list_tasks", "arguments": "{}"}}],
                    "stats": {},
                }
                return
            yield {"type": "turn", "content": "done", "tool_calls": [], "stats": {}}

        monkeypatch.setattr(agent_loop, "_stream_once", intermittent)
        monkeypatch.setattr(agent_loop.time, "sleep", lambda _s: None)

        list(
            agent_loop._run_turn(
                [], default_config(), "host", db, tools.host(db, language_server=False), max_rounds=40
            )
        )

        assert not [one for one in sent if agent_loop._LANDING_DIRECTIVE in one], (
            "three separated blips were treated as one outage — the count is not being reset"
        )


class TestWhatTheServerCallsWrongStillLands:
    def test_a_401_goes_straight_to_the_smaller_request(self, db: Path, monkeypatch):
        """Nothing transient about it: the same request will be refused the same way.

        Landing's narrower toolset is the recovery here rather than the damage — it is a
        genuinely different, smaller request, and one that may get through.
        """
        sent: list[str] = []

        def refused(convo, config, host, tools=None, tool_choice="auto", routing=None):
            sent.append(str((convo[-1] if convo else {}).get("content") or ""))
            if len(sent) == 1:
                yield {"type": "error", "message": "Cloud model returned 400: messages[3] is malformed"}
            else:
                yield {"type": "turn", "content": "wrote it down", "tool_calls": [], "stats": {}}

        monkeypatch.setattr(agent_loop, "_stream_once", refused)
        monkeypatch.setattr(agent_loop.time, "sleep", lambda _s: None)

        list(
            agent_loop._run_turn(
                [], default_config(), "host", db, tools.host(db, language_server=False), max_rounds=40
            )
        )

        assert len(sent) >= 2
        assert agent_loop._LANDING_DIRECTIVE in sent[1], "a 400 is not a blip to carry on from"


class TestWhatHeAlreadySaidSurvivesTheRound:
    def test_a_half_answer_is_in_the_history_the_next_round_reads(self, db: Path, monkeypatch):
        """The directive's own claim, made true.

        `_ROUND_FAILED_DIRECTIVE` tells him "nothing you did earlier in this turn was lost, it is
        all still above". That was false in exactly the case it is written for: a stream that
        dies *after* it has started answering. `content` comes from the terminating `turn` event,
        which never arrives, so the half-answer went to the screen and to the transcript and was
        missing from the list the next round is built from — and he was then told, in as many
        words, that it was still there.
        """
        rounds: list[list[dict]] = []

        def dies_mid_sentence(convo, config, host, tools=None, tool_choice="auto", routing=None):
            rounds.append([dict(m) for m in convo])
            if len(rounds) == 1:
                yield {"type": "delta", "role": "text", "text": "I checked the config and "}
                yield {"type": "delta", "role": "text", "text": "the timeout is set to 30s"}
                yield {"type": "error", "message": "Cloud model returned 502: upstream unavailable"}
            else:
                yield {"type": "turn", "content": "as I was saying", "tool_calls": [], "stats": {}}

        monkeypatch.setattr(agent_loop, "_stream_once", dies_mid_sentence)
        monkeypatch.setattr(agent_loop.time, "sleep", lambda _s: None)

        list(
            agent_loop._run_turn(
                [{"role": "user", "content": "check the config"}],
                default_config(),
                "host",
                db,
                tools.host(db, language_server=False),
                max_rounds=40,
            )
        )

        assert len(rounds) >= 2, "the turn did not carry on from the dead round"
        second = rounds[1]
        said = [m for m in second if m.get("role") == "assistant"]
        assert said, f"his half-answer is gone: {[m['role'] for m in second]}"
        assert said[-1]["content"] == "I checked the config and the timeout is set to 30s"
        # And it is above the directive that claims it is above.
        assert second[-1]["content"] == agent_loop._ROUND_FAILED_DIRECTIVE

    def test_a_round_that_said_nothing_adds_nothing(self, db: Path, monkeypatch):
        """A dead round that never spoke leaves no assistant message, as before — an empty one
        would be a message a provider is entitled to refuse."""
        rounds: list[list[dict]] = []

        def dies_silently(convo, config, host, tools=None, tool_choice="auto", routing=None):
            rounds.append([dict(m) for m in convo])
            if len(rounds) <= _KILLS_A_ROUND:
                yield {"type": "error", "message": "Cloud model returned 502: upstream unavailable"}
            else:
                yield {"type": "turn", "content": "carried on", "tool_calls": [], "stats": {}}

        monkeypatch.setattr(agent_loop, "_stream_once", dies_silently)
        monkeypatch.setattr(agent_loop.time, "sleep", lambda _s: None)

        list(
            agent_loop._run_turn(
                [{"role": "user", "content": "go"}],
                default_config(),
                "host",
                db,
                tools.host(db, language_server=False),
                max_rounds=40,
            )
        )

        after = rounds[_KILLS_A_ROUND]
        assert not [m for m in after if m.get("role") == "assistant"]
