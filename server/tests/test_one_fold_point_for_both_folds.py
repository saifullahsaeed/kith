"""One fold point, measured one way, between turns and within one.

There were two, and they agreed only on the number. Both folded at 80% of the window — 838,860
tokens on the million-token model this install runs — and they measured different things: the
fold between turns sized every message whole, tool-call arguments included, while the check
within a turn with no known window counted `content` strings and missed the arguments, about
half of a replayed history. Measured Sep 13-27, only 8 turns folded within themselves in two
weeks, long chats swung between ~260k and ~800k tokens, and rounds over 300k were 48% of all
rounds and 80% of the spend. A cached 800k round cost $0.0024 on DeepSeek; an uncached one $0.12.

So `fold_at_tokens` is the fold point for both, capped at 80% of a small window, and both compare
the whole request against it in tokens at the ratio the provider's bills calibrated.
"""

from __future__ import annotations

import json

from kith.domain.chat import Config
from kith.domain.tuning import for_key
from kith.llm import ledger
from kith.llm.budget import ContextBudget, conversation_chars
from kith.services import agent_loop, brief, history, tuning

_SCHEMAS = [{"type": "function", "function": {"name": "write_file", "description": "writes a file"}}]


def _frame(part: list[dict]) -> list[dict]:
    return [{"role": "system", "content": "You are Kith."}, *part]


def _cfg(window: int) -> Config:
    return Config(
        model="m", num_ctx=0, num_predict=0, system="You are Kith.", think=False, context_window=window
    )


class TestThePoint:
    def test_it_is_two_hundred_thousand_tokens_by_default(self):
        assert for_key("fold_at_tokens").default == 200_000
        assert history.fold_at(1_050_000) == 200_000

    def test_a_small_model_folds_at_80_percent_of_its_own_window(self):
        assert history.fold_at(40_960) == 32_768

    def test_the_old_knob_is_gone(self):
        """`history_max_chars` was the fold point for an unrecorded window, in characters. The one
        setting replaced it; a stored value is reported as unread and ignored."""
        import pytest

        with pytest.raises(ValueError):
            for_key("history_max_chars")


def _written(size: int) -> list[dict]:
    """A turn whose bulk is in what he wrote, not in anything he read: the file rides on the
    call's arguments, and the result is one line."""
    convo: list[dict] = [{"role": "system", "content": "persona"}, {"role": "user", "content": "build it"}]
    for i in range(30):
        arguments = json.dumps({"path": f"page-{i}.html", "content": "<p>" + "x" * size + "</p>"})
        convo.append(
            {
                "role": "assistant",
                "tool_calls": [{"id": f"c{i}", "function": {"name": "write_file", "arguments": arguments}}],
            }
        )
        convo.append({"role": "tool", "tool_name": "write_file", "content": '{"ok": true}'})
    return convo


def _drive(generator):
    events = []
    while True:
        try:
            events.append(next(generator))
        except StopIteration as done:
            return events, done.value


class TestWithinATurnItCountsWhatIsWritten:
    def test_a_turn_heavy_with_written_files_is_past_the_point_even_with_no_window(self, monkeypatch):
        """With no recorded window the check used to count `content` strings only, and a
        tool-calling message has none — so thirty files written were invisible to it."""
        tuning.apply({"fold_at_tokens": 20_000})
        convo = _written(4_000)  # ~120k characters, all of it in arguments
        asked: list[int] = []
        monkeypatch.setattr(agent_loop, "_stream_once", lambda *a, **k: asked.append(1) or iter([]))

        events, _ = _drive(
            agent_loop._make_room(
                convo,
                _SCHEMAS,
                ContextBudget(window=0),
                take_reading=lambda: ledger.take(convo, _SCHEMAS),
                config=Config(model="m", num_ctx=0, num_predict=0, system="", think=False),
                host="",
            )
        )

        assert "compacting" in [event["type"] for event in events]
        assert asked, "a fold was attempted"


class TestPastThePointIsNotShortOfRoom:
    def test_a_turn_out_of_folds_is_not_shaved_while_there_is_room(self, monkeypatch):
        """Shaving stubs one more call's arguments every round, which moves the point the cached
        prefix breaks at on every round for the rest of the turn. At 80% of the window that was
        the price of not dying on a 400; at a fold point a fifth of the way in, it is a cache
        miss bought for nothing. A turn that has used its folds grows until room is short."""
        tuning.apply({"fold_at_tokens": 20_000})
        convo = _written(4_000)
        before = [json.dumps(m) for m in convo]
        monkeypatch.setattr(agent_loop, "_stream_once", lambda *a, **k: iter([]))
        room = ContextBudget(window=1_000_000)
        room.observe(prompt_tokens=33_000, chars=conversation_chars(convo, _SCHEMAS))

        _drive(
            agent_loop._make_room(
                convo,
                _SCHEMAS,
                room,
                take_reading=lambda: ledger.take(convo, _SCHEMAS, window=1_000_000),
                config=Config(
                    model="m", num_ctx=0, num_predict=0, system="", think=False, context_window=1_000_000
                ),
                host="",
                folds=agent_loop._MAX_FOLDS,
            )
        )

        assert [json.dumps(m) for m in convo] == before


class TestBothFoldsAgreeWhereThePointIs:
    def _history(self, size: int) -> list[dict]:
        out: list[dict] = []
        for i in range(10):
            out.append({"role": "user", "content": f"q{i}"})
            out.append(
                {
                    "role": "assistant",
                    "tool_calls": [{"function": {"name": "write_file", "arguments": {"body": "y" * size}}}],
                }
            )
            out.append({"role": "tool", "tool_name": "write_file", "content": '"ok"'})
            out.append({"role": "assistant", "content": f"a{i}"})
        return out

    def _between_turns_folds(self, messages: list[dict], window: int) -> bool:
        asked: list[bool] = []
        history.compact(
            messages,
            lambda _request: asked.append(True) or "",
            max_chars=history._budget(
                _cfg(window), "", history._head_chars(_cfg(window), _frame, _SCHEMAS, 0)
            ),
            keep_recent=2,
        )
        return bool(asked)

    def _within_a_turn_folds(self, messages: list[dict], window: int) -> bool:
        prompt = _frame(messages)
        return history.fold_due(ledger.take(prompt, _SCHEMAS, window=window).used, window)

    def test_just_under_neither_folds_and_just_over_both_do(self):
        """The same history, framed as the turn sends it, judged by both checks. A disagreement
        either way is the failure: under here and over there folds within every turn — a fold
        thrown away when the turn ends — and never between them, where it is kept."""
        tuning.apply({"fold_at_tokens": 20_000})
        window = 1_050_000
        room = history.fold_at(window) * 3.7
        per_call = int(room / 10)
        for size, expected in ((int(per_call * 0.95), False), (int(per_call * 1.05), True)):
            messages = self._history(size)
            assert self._between_turns_folds(messages, window) is expected, size
            assert self._within_a_turn_folds(messages, window) is expected, size


class TestAFoldLandsUnderThePoint:
    def test_recent_turns_too_big_to_keep_whole_are_folded_too(self):
        """Six recent turns protected whatever they weigh, and a turn of heavy reading replays its
        results whole for the two turns after it: a fold that kept them all could leave the prompt
        still past the point, and fold again on the very next turn. The tail gets half the room."""
        convo: list[dict] = []
        for i in range(8):
            convo.append({"role": "user", "content": f"q{i}"})
            convo.append({"role": "assistant", "content": "z" * 4_000})
        budget = 20_000

        kept, made = history.compact(
            convo,
            lambda _request: brief.carried("", "## What was done\nall of it"),
            max_chars=budget,
            keep_recent=6,
            keep_chars=budget // 2,
        )

        assert made is not None
        assert conversation_chars(kept) <= budget
