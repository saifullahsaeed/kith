"""What happens as a conversation approaches the model's context window.

Before this, nothing watched the total. A turn that outgrew the window came back as
`Cloud model returned 400: …` and ended — no compaction, no retry, no chance to write down
what it had. The window was read once when the model was chosen and never consulted again.

The design has three parts and each is a place a previous attempt went wrong:

* **estimate from the wire, not a ruler** — no tokenizer is installed, and a fixed
  chars-per-token is not good enough to build a threshold on. Measured against this repo's
  own transcripts, transcript bytes predict billed prompt tokens at a median of 0.17,
  because the prompt is dominated by a persona and tool schemas no transcript contains;
* **an absolute threshold, not a fraction** — 85% of a 32k model leaves less room than one
  default answer, and 85% of a million leaves 150,000 tokens going spare;
* **trim history, never the toolset** — the obvious move was to reuse the `landing` latch,
  which is one-way. Tripping it at round 3 of a 40-round turn removes shell and every file
  tool for the remaining 37, leaving him unable to do the thing he was asked.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from kith.llm import budget
from kith.llm.budget import ContextBudget, conversation_chars, message_chars
from kith.services.agent_loop import _DROPPED_NOTE, _drop_oldest_exchange


def exchange(call_id: str, size: int = 100) -> list[dict]:
    """One assistant tool-call and the result that answered it."""
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": call_id, "function": {"name": "read_file", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": call_id, "tool_name": "read_file", "content": "x" * size},
    ]


class TestMeasuringWhatWasSent:
    def test_a_picture_is_costed_not_measured(self):
        """A data URI is ~600KB of characters and a few hundred tokens. Measuring its length
        would put the estimate out by three orders of magnitude, in the direction that makes
        the guard fire on every round forever."""
        picture = {
            "role": "user",
            "content": [
                {"type": "text", "text": "Here is the page:"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 600_000}},
            ],
        }
        assert message_chars(picture) < 10_000

    def test_ordinary_messages_are_measured_normally(self):
        plain = {"role": "user", "content": "hello"}
        assert message_chars(plain) == len(json.dumps(plain))

    def test_the_tool_block_counts_too(self):
        """It is sent on every round and is ~7,840 tokens of the prompt. Leaving it out would
        under-count by more than most conversations."""
        convo = [{"role": "user", "content": "hi"}]
        schemas = [{"function": {"name": "x", "description": "y" * 5_000}}]
        assert conversation_chars(convo, schemas) > conversation_chars(convo) + 4_000


class TestCalibration:
    def test_the_ratio_comes_from_what_the_provider_charged(self):
        room = ContextBudget(window=100_000)
        room.observe(prompt_tokens=1_000, chars=4_200)
        assert room.chars_per_token == pytest.approx(4.2)

    def test_an_absurd_ratio_is_clamped_rather_than_believed(self):
        """Outside the band something is wrong with the arithmetic, not the model. A ratio of
        20 would silently switch the guard off; 0.5 would make it fire constantly."""
        room = ContextBudget(window=100_000)
        room.observe(prompt_tokens=1, chars=1_000_000)
        assert room.chars_per_token == budget.MAX_RATIO
        room.observe(prompt_tokens=1_000_000, chars=1)
        assert room.chars_per_token == budget.MIN_RATIO

    def test_a_nonsense_report_is_ignored(self):
        room = ContextBudget(window=100_000)
        room.observe(prompt_tokens=0, chars=5_000)
        assert room.chars_per_token == budget.SEED_CHARS_PER_TOKEN

    def test_the_projection_anchors_on_the_measurement(self):
        """Only the delta since the last round is estimated, so the error is on a percent or
        two of the prompt rather than on all of it."""
        room = ContextBudget(window=100_000)
        room.observe(prompt_tokens=10_000, chars=40_000)
        # 4,000 more characters at the measured 4.0 chars/token is 1,000 more tokens.
        assert room.projected(44_000) == 11_000

    def test_it_learns_how_much_a_round_grows(self):
        room = ContextBudget(window=100_000)
        room.observe(10_000, 40_000)
        room.observe(11_000, 44_000)
        assert room.biggest_growth == 4_000


class TestTheThreshold:
    def test_an_unknown_window_never_fires(self):
        """Ollama and custom endpoints report no window. Acting on a guessed one would trim a
        conversation that had plenty of room."""
        room = ContextBudget(window=0, reserve=1_000)
        room.observe(10_000, 40_000)
        assert room.is_tight(10_000_000) is False

    def test_nor_does_it_before_the_first_measurement(self):
        """There is nothing to anchor to yet, and acting on a pure estimate is how a turn
        gets trimmed on round one for no reason."""
        room = ContextBudget(window=1_000, reserve=100)
        assert room.is_tight(10_000_000) is False

    def test_a_roomy_conversation_is_not_tight(self):
        room = ContextBudget(window=1_000_000, reserve=8_000)
        room.observe(50_000, 200_000)
        assert room.is_tight(200_000) is False

    def test_one_that_will_not_fit_another_round_is(self):
        room = ContextBudget(window=100_000, reserve=8_000)
        room.observe(50_000, 200_000)
        room.observe(88_000, 352_000)  # growth of 152,000 chars = 38,000 tokens
        assert room.is_tight(352_000) is True

    def test_the_answer_reserve_is_part_of_it(self):
        """A prompt that fits exactly still fails, because the answer has to fit too."""
        room = ContextBudget(window=100_000, reserve=0)
        room.observe(99_000, 396_000)
        assert room.is_tight(396_000) is False
        room.reserve = 5_000
        assert room.is_tight(396_000) is True

    def test_a_small_window_is_judged_on_room_not_percentage(self):
        """85% of 32,000 leaves 4,800 tokens — less than one default answer. A fractional
        threshold calls that healthy."""
        room = ContextBudget(window=32_000, reserve=8_192)
        room.observe(27_000, 100_000)  # 84% full, and genuinely out of room
        assert room.is_tight(100_000) is True


class TestMakingRoom:
    def test_an_exchange_goes_whole(self):
        """A `tool` message whose `tool_call_id` has no matching call is a 400. Dropping half
        an exchange would turn running out of room into a different error entirely."""
        convo = [{"role": "user", "content": "go"}, *exchange("a"), *exchange("b"), *exchange("c")]
        assert _drop_oldest_exchange(convo) is True

        ids = [m.get("tool_call_id") for m in convo if m.get("role") == "tool"]
        calls = [c["id"] for m in convo if m.get("tool_calls") for c in m["tool_calls"]]
        assert sorted(ids) == sorted(calls), "a result was left without its call"
        assert "a" not in calls

    def test_the_request_and_persona_are_never_dropped(self):
        """The persona is the whole of what the prompt cache holds. Dropping it would free a
        few thousand tokens and cost the cached prefix on every remaining round."""
        convo = [{"role": "user", "content": "the actual request"}, *exchange("a"), *exchange("b")]
        _drop_oldest_exchange(convo)
        assert convo[0]["content"] == "the actual request"

    def test_it_leaves_a_note_saying_work_from_your_files(self):
        convo = [{"role": "user", "content": "go"}, *exchange("a"), *exchange("b")]
        _drop_oldest_exchange(convo)
        assert any(m.get("content") == _DROPPED_NOTE for m in convo)

    def test_the_note_is_byte_stable_however_many_go(self):
        """A note reading "4 earlier steps" becomes "5 earlier steps" and invalidates the
        prefix from that point on every round after — reintroducing, inside the trimming
        code, the bug the trimming code exists to avoid."""
        convo = [{"role": "user", "content": "go"}, *[m for i in "abcd" for m in exchange(i)]]
        _drop_oldest_exchange(convo)
        after_one = json.dumps([m for m in convo if m.get("_dropped")])
        _drop_oldest_exchange(convo)
        after_two = json.dumps([m for m in convo if m.get("_dropped")])
        assert after_one == after_two
        assert sum(1 for m in convo if m.get("_dropped")) == 1

    def test_the_last_exchange_is_never_dropped(self):
        """A turn with no evidence of what it just did is worse than one slightly over
        budget — and the next round would drop the round that was about to save the work."""
        convo = [{"role": "user", "content": "go"}, *exchange("only")]
        assert _drop_oldest_exchange(convo) is False

    def test_nothing_to_drop_says_so_rather_than_looping(self):
        assert _drop_oldest_exchange([{"role": "user", "content": "just a question"}]) is False

    def test_dropping_actually_shrinks_it(self):
        """By the size of the exchange, less the note that replaces it. Asserting a bare
        halving was wrong by the note's own 240 characters — worth stating as a number rather
        than a ratio, since what is being bought here is a specific amount of room."""
        convo = [{"role": "user", "content": "go"}, *exchange("a", 50_000), *exchange("b", 50_000)]
        before = conversation_chars(convo)

        _drop_oldest_exchange(convo)

        freed = before - conversation_chars(convo)
        assert freed > 49_000, "the exchange did not actually leave"
        assert freed < 50_500, "more than the one exchange went"


class TestTheBackstop:
    """When the guard is wrong — an unknown window, a provider counting differently — the
    400 still arrives. It should say what happened."""

    @pytest.mark.parametrize(
        "body",
        [
            "This model's maximum context length is 128000 tokens",
            "Requested 200000 tokens, exceeds the maximum",
            "input length and `max_tokens` exceed context limit",
            "prompt is too long: 250000 tokens",
        ],
    )
    def test_a_window_400_is_recognised(self, body):
        assert budget.looks_like_overflow(400, body) is True

    @pytest.mark.parametrize(
        "body",
        [
            "Reasoning is mandatory for this endpoint and cannot be disabled",
            "invalid api key",
            "Unsupported parameter(s): `usage`",
        ],
    )
    def test_and_another_kind_of_400_is_not(self, body):
        """Guessing permissively would make the loop hard-compact a conversation to fix a
        problem it does not have."""
        assert budget.looks_like_overflow(400, body) is False

    def test_a_different_status_is_never_it(self):
        assert budget.looks_like_overflow(429, "context length exceeded") is False
        assert budget.looks_like_overflow(500, "context length exceeded") is False

    def test_it_is_classified_before_the_reasoning_retry(self):
        """That retry fires on any 400 whose body merely contains "reasoning", and an
        overflow message often lists a reasoning-token breakdown. Left second, an
        over-budget request would be sent a second time before anything noticed."""
        import inspect

        source = inspect.getsource(__import__("kith.llm.openai_compat", fromlist=["stream_once"]).stream_once)
        overflow_at = source.index("looks_like_overflow")
        reasoning_at = source.index("_refuses_reasoning")
        assert overflow_at < reasoning_at


class TestTheLoopActuallyUsesIt:
    """The parts above are unit-tested. This drives `stream_agent` itself, because a
    correctly-built guard wired to nothing is the failure mode this whole session keeps
    finding — `reembed`, the allow-lists, the switch that set a flag.
    """

    def loop_with(self, monkeypatch, db, window: int, prompt_tokens: int, rounds: int):
        """Run a real turn against a fake provider that reports a fixed prompt size.

        Each round asks for one big tool result, so the conversation grows the way a real
        research turn does. Returns the conversation as it stood at each request.
        """
        from kith.services import agent_loop

        seen: list[list[dict]] = []
        calls = {"n": 0}

        def fake_stream(convo, config, host, tools=None, tool_choice="auto"):
            seen.append([dict(m) for m in convo])
            calls["n"] += 1
            stats = {"promptTokens": prompt_tokens, "responseTokens": 10}
            if calls["n"] >= rounds:
                return iter([{"type": "turn", "content": "done", "tool_calls": [], "stats": stats}])
            return iter(
                [
                    {
                        "type": "turn",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": f"c{calls['n']}",
                                "function": {"name": "read_file", "arguments": '{"path": "x"}'},
                            }
                        ],
                        "stats": stats,
                    }
                ]
            )

        monkeypatch.setattr(agent_loop, "_stream_once", fake_stream)
        monkeypatch.setattr(
            agent_loop.tools,
            "run_tool",
            lambda *a, **k: {"ok": True, "result": "y" * 40_000},
        )
        from kith.config import default_config

        config = replace(default_config(), context_window=window, num_predict=2_000)
        list(agent_loop.stream_agent([{"role": "user", "content": "go"}], config, "", db, max_rounds=rounds))
        return seen

    def test_a_roomy_window_is_never_trimmed(self, db, monkeypatch):
        seen = self.loop_with(monkeypatch, db, window=1_000_000, prompt_tokens=20_000, rounds=5)
        assert not any(m.get("_dropped") for convo in seen for m in convo), (
            "a conversation with room to spare was trimmed"
        )

    def test_a_tight_one_is(self, db, monkeypatch):
        """Same turn, same growth — only the window differs."""
        seen = self.loop_with(monkeypatch, db, window=30_000, prompt_tokens=20_000, rounds=5)
        assert any(m.get("_dropped") for convo in seen for m in convo), (
            "the conversation outgrew the window and nothing made room"
        )

    def test_and_the_turn_still_finishes(self, db, monkeypatch):
        """Trimming must not cost the answer. The point is a turn that completes, not one
        that fails more tidily."""
        seen = self.loop_with(monkeypatch, db, window=30_000, prompt_tokens=20_000, rounds=5)
        assert len(seen) == 5, "the turn stopped early"

    def test_an_unknown_window_is_left_alone(self, db, monkeypatch):
        """Ollama reports none. Guessing would trim a conversation with plenty of room."""
        seen = self.loop_with(monkeypatch, db, window=0, prompt_tokens=900_000, rounds=4)
        assert not any(m.get("_dropped") for convo in seen for m in convo)


class TestTheMeterMatchesWhatWasActuallySent:
    """Found live: a real turn showed 814,600 tokens on the meter (77.5% — under the fold's
    own 80% trigger, so no fold ran) while this loop's *other* threshold — `is_tight`, a
    separate absolute ceiling that also charges for the biggest round-to-round growth seen so
    far — fired right after and cut what was actually sent down to 408,313. The "context"
    event used to be yielded before that trim ran, so the number shown described a request
    that was never sent. Now it runs first: whatever "context" reports must always match the
    round that request actually was, trimmed or not.
    """

    def events_for(self, monkeypatch, db, window: int, prompt_tokens: int, rounds: int):
        """Like `TestTheLoopActuallyUsesIt.loop_with`, but keeps every yielded event, not
        just what was sent — the whole point here is checking the two against each other."""
        from kith.services import agent_loop

        seen: list[list[dict]] = []
        calls = {"n": 0}

        def fake_stream(convo, config, host, tools=None, tool_choice="auto"):
            seen.append([dict(m) for m in convo])
            calls["n"] += 1
            stats = {"promptTokens": prompt_tokens, "responseTokens": 10}
            if calls["n"] >= rounds:
                return iter([{"type": "turn", "content": "done", "tool_calls": [], "stats": stats}])
            return iter(
                [
                    {
                        "type": "turn",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": f"c{calls['n']}",
                                "function": {"name": "read_file", "arguments": '{"path": "x"}'},
                            }
                        ],
                        "stats": stats,
                    }
                ]
            )

        monkeypatch.setattr(agent_loop, "_stream_once", fake_stream)
        monkeypatch.setattr(
            agent_loop.tools,
            "run_tool",
            lambda *a, **k: {"ok": True, "result": "y" * 40_000},
        )
        from kith.config import default_config

        config = replace(default_config(), context_window=window, num_predict=2_000)
        events = list(
            agent_loop.stream_agent([{"role": "user", "content": "go"}], config, "", db, max_rounds=rounds)
        )
        contexts = [e["context"] for e in events if e.get("type") == "context"]
        return seen, contexts

    def test_a_trimmed_round_reports_what_it_actually_sent(self, db, monkeypatch):
        """A window small enough that `is_tight` trims most rounds, but roomy enough — and
        with growth small enough — that the fold's own 80% share never trips. If the two
        thresholds ever disagree, this is where it shows: the display must follow whichever
        one actually acted.
        """
        seen, contexts = self.events_for(monkeypatch, db, window=30_000, prompt_tokens=20_000, rounds=5)
        assert any(m.get("_dropped") for convo in seen for m in convo), (
            "nothing was trimmed — this test needs a round where it was, to mean anything"
        )
        assert len(seen) == len(contexts), "one context reading is expected per round"

        for convo_sent, reading in zip(seen, contexts, strict=False):
            sent_chars = conversation_chars(convo_sent)
            # The ratio drifts round to round as real stats come in, so this isn't an exact
            # equality — it is a ceiling. What matters is the direction of the old bug: the
            # displayed size must never come from a *bigger* convo than the one just sent.
            implied_chars_upper_bound = reading["used"] * budget.MAX_RATIO
            assert sent_chars <= implied_chars_upper_bound * 1.05, (
                f"reported {reading['used']} tokens implies at most ~{implied_chars_upper_bound:.0f} "
                f"chars, but {sent_chars} chars were actually sent — the meter is describing a "
                "smaller request than the one that went out"
            )
