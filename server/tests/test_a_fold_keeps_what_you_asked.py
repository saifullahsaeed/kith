"""A fold may compress what he said and did. It may not compress what you asked.

From a real fold, on a real conversation. Six turns went in and this came out:

    [Summary of the earlier part of this conversation]
    CI optimization is pushed in `ce06d61`.
    - Two balanced test shards run in parallel.
    …

Every word of it true, and it lost every question that had been asked — including one about
milestones, the UI and the extension system that had never been answered. The instruction the
summariser runs under explicitly asks it to preserve "unfinished threads"; it could not weigh a
thread it could barely see.

The reason is in the numbers the context screen was showing at the time:

    tool       38%   14.8k tokens
    system     34%   13.3k
    assistant  27%   10.6k
    user      0.3%     107      <- everything the person had said

A summariser handed a transcript that is 99.7% one party faithfully summarises that party. This
is the same failure as a new message losing to the previous task in the live prompt, one layer
down: the person outweighed by volume. Neither is fixed by asking the model to try harder, so
the questions are not offered to its judgement at all.

**It costs about one per cent**, which is the measurement that makes the rule obvious rather
than clever. On the two longest conversations here — 3,133 messages and 2.27M characters, and
453 messages and 1.03M — everything the person said came to 1.10% and 0.45% of the total.
"""

from __future__ import annotations

from kith.services import history


def _loud(rounds: int = 4) -> list[dict]:
    """A conversation shaped like the real one: short questions, enormous answers."""
    convo: list[dict] = []
    for i in range(rounds):
        convo.append({"role": "user", "content": f"question {i}"})
        convo.append({"role": "assistant", "content": f"answer {i} " + "detail " * 300})
        convo.append({"role": "tool", "content": "tool output " * 400})
    return convo


def _brief(_text: str) -> str:
    return "CI optimization is pushed in ce06d61."


def _roles(messages: list[dict]) -> list[str]:
    return [str(m.get("role")) for m in messages]


def _asked(messages: list[dict]) -> list[str]:
    return [str(m.get("content") or "") for m in messages if m.get("role") == "user"]


class TestTheQuestionsSurvive:
    def test_every_question_from_the_folded_part_is_still_there(self):
        convo = _loud()
        kept, _ = history.compact(convo, _brief, {}, max_chars=1, keep_recent=1)
        assert _asked(kept) == ["question 0", "question 1", "question 2", "question 3"]

    def test_they_are_word_for_word_rather_than_summarised(self):
        """The whole point. A paraphrase of what someone asked is a paraphrase of the only
        thing in the transcript that says what they wanted."""
        convo = [
            {"role": "user", "content": "ok whats on the milestone, ui and backend, whole extension system"},
            {"role": "assistant", "content": "CI is green. " + "shard timings. " * 400},
            {"role": "user", "content": "?"},
            {"role": "assistant", "content": "still CI. " * 400},
        ]
        kept, _ = history.compact(convo, _brief, {}, max_chars=1, keep_recent=1)
        assert "ok whats on the milestone, ui and backend, whole extension system" in _asked(kept)

    def test_his_own_output_is_still_compressed(self):
        """This is not "stop folding". The mass is his prose and his tool results, and that is
        exactly what the fold is for."""
        convo = _loud()
        kept, _ = history.compact(convo, _brief, {}, max_chars=1, keep_recent=1)
        assert len(kept) < len(convo)
        assert sum(1 for m in kept if m.get("role") == "tool") <= 1

    def test_the_brief_still_leads(self):
        convo = _loud()
        kept, _ = history.compact(convo, _brief, {}, max_chars=1, keep_recent=1)
        assert kept[0]["role"] == "system"
        assert history._SUMMARY_HEADER in str(kept[0]["content"])

    def test_they_sit_between_the_brief_and_the_recent_turns(self):
        """Chronological. The brief is the oldest thing, then what was asked, then whatever was
        kept verbatim — anything else reads as the questions happening after the recent work."""
        convo = _loud()
        kept, _ = history.compact(convo, _brief, {}, max_chars=1, keep_recent=1)
        roles = _roles(kept)
        assert roles[0] == "system"
        assert roles[1] == "user"

    def test_reusing_an_existing_brief_keeps_them_too(self):
        """The cheap path — no model call — had the same hole, and it is the path most turns
        actually take."""
        convo = _loud()
        _, made = history.compact(convo, _brief, {}, max_chars=1, keep_recent=1)
        assert made is not None
        again, fresh = history.compact(convo, _brief, made, max_chars=10_000_000, keep_recent=1)
        assert fresh is None, "this is the reuse path, not a second fold"
        assert _asked(again), "the questions survive the reuse path as well"


class TestItStaysCheap:
    #: Tested against `_what_they_asked` rather than through `compact`, because that is where
    #: the ceiling is. A whole fold cannot reach it: `_capped_cut` limits how much territory one
    #: summarisation call reads, so a conversation that large is folded over several calls and
    #: most of it is still verbatim tail — which is right, and would make this assert nothing.
    def test_a_conversation_of_nothing_but_questions_is_bounded(self):
        """The one shape that could make this expensive — someone pasting a document per
        message. Measured at ~1% in practice, so this never binds; it exists so that it cannot."""
        convo = [{"role": "user", "content": f"{i} " + "x" * 1_000} for i in range(400)]
        carried = history._what_they_asked(convo, len(convo))
        spent = sum(len(str(m.get("content") or "")) for m in carried)
        assert spent <= history._KEEP_ASKED_CHARS * 1.1
        assert len(carried) < len(convo), "the ceiling actually bit"

    def test_a_ceiling_drops_the_oldest_not_the_newest(self):
        """An old question that has been answered is the one worth losing."""
        convo = [{"role": "user", "content": f"ask-{i} " + "x" * 1_000} for i in range(400)]
        carried = [str(m["content"]).split()[0] for m in history._what_they_asked(convo, len(convo))]
        assert carried[-1] == "ask-399", carried[-1]
        assert "ask-0" not in carried

    def test_one_enormous_question_is_still_carried(self):
        """A ceiling that dropped everything would be worse than no ceiling: the newest is kept
        even when it alone exceeds the budget."""
        convo = [{"role": "user", "content": "x" * (history._KEEP_ASKED_CHARS * 2)}]
        assert len(history._what_they_asked(convo, 1)) == 1

    def test_nothing_changes_for_a_conversation_too_short_to_fold(self):
        convo = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
        kept, made = history.compact(convo, _brief, {}, max_chars=1_000_000, keep_recent=1)
        assert kept == convo and made is None
