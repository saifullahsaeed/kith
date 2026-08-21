"""A round with nothing in it was not given room to say anything.

Off a real conversation — 2026-08-20, `20260820-223844780-a6187c.jsonl`. Two turns, six
minutes apart, both died on "google/gemini-3.7-flash returned an empty response 2 times in a
row — all reasoning, no answer and no tool call. Try again, or switch model." The transcript
and OpenRouter's own log of the same four rounds:

    21:44:58  prompt 128,981  out 5,139  finish tool_calls   (a 5k-token python script)
    21:45:43  prompt       0  out     0  45.3s               (no usage block at all)
    21:46:37  prompt 268,367  out   572  finish LENGTH       $0.0592 -> the turn died
    21:48:17  prompt 124,847  out 5,832  finish tool_calls   ("try again")
    21:49:09  prompt       0  out     0  51.9s
    21:49:59  prompt 259,993  out   336  finish LENGTH       $0.0570 -> the turn died

The provider said `length` on both of the rounds the loop called empty. Nothing read it —
`finish_reason` arrived on every response this project has ever made and was dropped in the
stream parser — so "he was cut off" and "he had nothing to say" were the same event, and the
turn ended by advising a change of model.

The model was not the problem. The install was sending `max_tokens: 8192` (`num_predict`, the
setting labelled "Longest single answer") with `reasoning: {effort: "max"}` beside it, and
OpenRouter documents effort as a *share of max_tokens*: max ~95%, high ~80%, medium ~50%. So
thinking held a 7,782-token claim on 8,192 and the reply got what thinking did not want — as
little as 410 tokens — on a turn whose every productive round was emitting 5,000-6,500-token
python scripts. Their own docs carry the rule it broke: "max_tokens must be strictly higher
than the reasoning budget to ensure there are tokens available for the final response after
thinking."

Put to the live provider on 2026-08-21, one prompt asking for a long script, both payloads:

    max_tokens 8,192  + reasoning {effort: max}       finish LENGTH   7,612 chars, cut off
    max_tokens 40,960 + reasoning {max_tokens: 32768} finish stop    63,520 chars, complete

Two fixes, two layers, one test file because they are one bug:

* the ceiling on the wire is the answer cap *plus* the thinking budget, never the cap alone,
  so "Longest single answer" means the answer (`llm.openai_compat`);
* a nothing-round whose provider said `length` is reported as the cut-off it is, once, rather
  than nudged and then blamed on the model (`services.agent_loop`).
"""

from __future__ import annotations

from pathlib import Path

from kith import tools
from kith.config import default_config
from kith.domain.chat import Config
from kith.llm.openai_compat import (
    MAX_THINKING_TOKENS,
    REASONING_EFFORTS,
    REASONING_SHARE,
    _reasoning_options,
    output_ceiling,
    thinking_budget,
)
from kith.services import agent_loop

#: What the install that produced the transcript above was set to.
CAP = 8_192


def _config(effort: str = "max", cap: int = CAP, think: bool = True) -> Config:
    return Config(
        model="google/gemini-3.7-flash",
        num_ctx=1_048_576,
        num_predict=cap,
        system="",
        think=think,
        effort=effort,
    )


class TestTheAnswerKeepsItsCap:
    """The invariant the old payload broke, stated once and checked at every effort level."""

    def test_the_reply_is_never_left_with_less_than_the_cap(self):
        for effort in REASONING_EFFORTS:
            config = _config(effort=effort)
            left = output_ceiling(config) - thinking_budget(config)
            assert left >= CAP, f"{effort} leaves the answer {left} tokens of a {CAP} cap"

    def test_the_share_is_taken_out_of_the_ceiling_not_out_of_the_answer(self):
        """The arithmetic, at the level that failed. Thinking's claim at "max" is 95% of
        `max_tokens`: of 8,192 that is 7,782, leaving as little as 410 to answer in. The budget
        this sends is explicit instead, so what is left over is the cap itself."""
        config = _config(effort="max")
        assert thinking_budget(config) == MAX_THINKING_TOKENS
        assert output_ceiling(config) == MAX_THINKING_TOKENS + CAP
        assert int(CAP * REASONING_SHARE["max"]) == 7_782  # thinking's claim, before
        assert CAP - int(CAP * REASONING_SHARE["max"]) == 410  # the least the reply was left

    def test_anthropics_rule_holds_by_construction(self):
        """ "max_tokens must be strictly higher than the reasoning budget" — their words, and
        true at every level rather than at the ones we happened to try."""
        for effort in REASONING_EFFORTS:
            config = _config(effort=effort)
            assert output_ceiling(config) > thinking_budget(config)

    def test_a_bigger_answer_cap_gets_a_bigger_budget_not_a_bigger_share(self):
        """The two numbers scale together up to the clamp, so raising "Longest single answer"
        cannot quietly cost thinking room."""
        assert thinking_budget(_config(effort="medium", cap=1_000)) == 1_000
        assert thinking_budget(_config(effort="medium", cap=4_000)) == 4_000
        assert thinking_budget(_config(effort="low", cap=8_000)) == 2_000

    def test_no_budget_is_asked_for_that_no_model_would_grant(self):
        """Inverted, "max" wants 19x the cap — 155,648 tokens on a default install, and over
        a million on a generous one. A provider rejects a request like that rather than
        clamping it, so the clamp is ours."""
        assert thinking_budget(_config(effort="max", cap=64_000)) == MAX_THINKING_TOKENS
        assert thinking_budget(_config(effort="max", cap=CAP)) == MAX_THINKING_TOKENS


class TestWhenThereIsNothingToProtect:
    def test_thinking_turned_off_leaves_the_ceiling_exactly_as_it_was(self):
        assert output_ceiling(_config(effort="none")) == CAP
        assert output_ceiling(_config(effort="", think=False)) == CAP

    def test_no_cap_means_no_ceiling_and_nothing_to_starve(self):
        """`num_predict` -1 is the "no limit" sentinel: with no `max_tokens` on the request
        there is no share for thinking to be taken out of, so effort goes as it always did."""
        config = _config(effort="max", cap=-1)
        assert output_ceiling(config) == 0
        assert thinking_budget(config) == 0
        assert _reasoning_options(config) == {"reasoning": {"effort": "max"}}

    def test_an_unstated_effort_still_reserves_room(self):
        """A default install says only `think: true` and leaves the amount to the provider.
        That is the same trap — a provider deciding to think hard inside a ceiling that is only
        as big as the answer — so the middle of the scale is reserved anyway."""
        config = _config(effort="", think=True)
        assert thinking_budget(config) == CAP
        assert output_ceiling(config) == 2 * CAP
        assert _reasoning_options(config) == {"reasoning": {"enabled": True}}


class TestWhatGoesOnTheWire:
    def test_a_budget_is_sent_rather_than_a_share_when_there_is_a_cap(self):
        """The only spelling that bounds thinking independently of the ceiling it shares with
        the answer. One spelling per request, as before — `effort` and `max_tokens` are the
        same field and sending both leaves the choice to the provider."""
        assert _reasoning_options(_config(effort="max")) == {"reasoning": {"max_tokens": MAX_THINKING_TOKENS}}
        assert _reasoning_options(_config(effort="medium")) == {"reasoning": {"max_tokens": CAP}}

    def test_off_is_still_said_as_effort(self):
        """There is no budget that means "do not think", so "none" stays a level."""
        assert _reasoning_options(_config(effort="none")) == {"reasoning": {"effort": "none"}}

    def test_the_ceiling_reaches_the_request_body(self, monkeypatch):
        """A pure function nobody calls bounds nothing."""
        from kith.llm import openai_compat

        captured: dict = {}

        class FakeResp:
            status_code = 200
            encoding = "utf-8"
            text = ""

            def iter_lines(self, decode_unicode=True):
                return iter(['data: {"choices":[{"delta":{"content":"ok"}}]}', "data: [DONE]"])

            def close(self):
                pass

        def fake_post(url, json=None, headers=None, stream=None, timeout=None):
            captured["json"] = json
            return FakeResp()

        monkeypatch.setattr(openai_compat.requests, "post", fake_post)
        config = Config(
            model="google/gemini-3.7-flash",
            num_ctx=1_048_576,
            num_predict=CAP,
            system="",
            think=True,
            effort="max",
            base_url="https://openrouter.ai/api/v1",
            api_key="k",
            session_id="s",
        )

        list(openai_compat.stream_once([{"role": "user", "content": "hi"}], config))

        assert captured["json"]["max_tokens"] == CAP + MAX_THINKING_TOKENS
        assert captured["json"]["reasoning"] == {"max_tokens": MAX_THINKING_TOKENS}


class TestTheProviderSaysWhyItStopped:
    def test_the_finish_reason_is_read_off_the_stream(self, monkeypatch):
        """It was on every response and read nowhere, which is the whole reason a cut-off
        round could be reported as a silent one."""
        from kith.llm import openai_compat

        class FakeResp:
            status_code = 200
            encoding = "utf-8"
            text = ""

            def iter_lines(self, decode_unicode=True):
                return iter(
                    [
                        'data: {"id":"gen-1755","choices":[{"delta":{"reasoning":"..."},'
                        '"finish_reason":null}]}',
                        'data: {"id":"gen-1755","choices":[{"delta":{},"finish_reason":"length"}]}',
                        "data: [DONE]",
                    ]
                )

            def close(self):
                pass

        monkeypatch.setattr(openai_compat.requests, "post", lambda *a, **k: FakeResp())
        config = Config(
            model="m", num_ctx=0, num_predict=0, system="", think=False, base_url="x", api_key="k"
        )

        turn = [
            e
            for e in openai_compat.stream_once([{"role": "user", "content": "hi"}], config)
            if e["type"] == "turn"
        ]

        assert turn[0]["stats"]["finishReason"] == "length"
        # And the provider's id for the round, which is what makes this row and the row in
        # their log the same object rather than two things that look alike.
        assert turn[0]["stats"]["generationId"] == "gen-1755"

    def test_a_local_model_says_it_the_same_way(self):
        """Ollama calls the field `done_reason` and hits `num_predict` the same way a hosted
        model hits `max_tokens`, so the loop must not have to know which transport it is on."""
        from kith.llm.ollama import _stats_from_done

        assert _stats_from_done({"done": True, "done_reason": "length"})["finishReason"] == "length"


def _rounds(*responses: dict):
    """One canned `turn` event per round; the last one repeats if asked for again."""
    calls: list[int] = []

    def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
        calls.append(1)
        yield dict(responses[min(len(calls) - 1, len(responses) - 1)], type="turn")

    return fake, calls


def _truncated() -> dict:
    """What the 21:46:37 round looked like once parsed: all reasoning, cut off."""
    return {
        "content": "",
        "tool_calls": [],
        "stats": {"responseTokens": 572, "reasoningTokens": 572, "finishReason": "length"},
    }


def _silent() -> dict:
    """The same emptiness with no reason given — the case the old branch was written for."""
    return {"content": "", "tool_calls": [], "stats": {"responseTokens": 1468, "reasoningTokens": 1468}}


def _run(fake, monkeypatch, db: Path, max_rounds: int = 6):
    monkeypatch.setattr(agent_loop, "_stream_once", fake)
    monkeypatch.setattr(agent_loop.time, "sleep", lambda _s: None)
    return list(
        agent_loop._run_turn(
            [], default_config(), "host", db, tools.host(db, language_server=False), max_rounds=max_rounds
        )
    )


class TestTheTurnSaysWhatActuallyHappened:
    def test_a_cut_off_round_is_not_nudged_and_not_repeated(self, db: Path, monkeypatch):
        """The nudge — "answer in plain text, or call a tool, this round" — asks for something
        he never got as far as choosing. A second attempt is the same request against the same
        ceiling: another minute, another six cents, the same silence."""
        fake, calls = _rounds(_truncated())
        events = _run(fake, monkeypatch, db)

        assert len(calls) == 1, "the doomed round must not be sent twice"
        assert [e for e in events if e["type"] == "error"]

    def test_the_message_names_the_ceiling_and_not_the_model_as_the_cause(self, db: Path, monkeypatch):
        """ "Try again, or switch model" sent someone hunting for a better model, and every
        model would have done the same with 410 tokens to answer in."""
        fake, _calls = _rounds(_truncated())
        message = next(e for e in _run(fake, monkeypatch, db) if e["type"] == "error")["message"]

        assert "cut off" in message
        assert "output ceiling" in message
        assert "Longest single answer" in message
        assert "switch model" not in message
        assert "empty response" not in message

    def test_a_round_cut_off_after_saying_something_is_still_an_answer(self, db: Path, monkeypatch):
        """`length` only means the turn is over when there is nothing to show for it. Prose
        that ran out of room is prose, and the turn ends on it as it always has."""
        fake, calls = _rounds(
            {"content": "here is the sc", "tool_calls": [], "stats": {"finishReason": "length"}}
        )
        events = _run(fake, monkeypatch, db)

        assert len(calls) == 1
        assert not [e for e in events if e["type"] == "error"]


class TestTheSilentCaseIsUnchanged:
    def test_no_reason_given_still_means_go_again_once(self, db: Path, monkeypatch):
        """`test_a_silent_round_is_not_a_finished_turn` owns this behaviour; this only pins
        that the new branch did not swallow it."""
        fake, calls = _rounds(_silent(), {"content": "here it is", "tool_calls": [], "stats": {}})
        events = _run(fake, monkeypatch, db)

        assert len(calls) == 2
        assert not [e for e in events if e["type"] == "error"]

    def test_and_two_in_a_row_still_ends_by_naming_the_model(self, db: Path, monkeypatch):
        fake, calls = _rounds(_silent())
        events = _run(fake, monkeypatch, db)

        assert len(calls) == 2
        assert "empty response" in next(e for e in events if e["type"] == "error")["message"]
