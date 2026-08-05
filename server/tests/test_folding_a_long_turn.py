"""Folding the middle of a turn into notes, and never breaking the conversation to do it.

Two hard constraints, and getting either wrong is worse than not folding at all:

* **Tool pairing.** An assistant message carrying `tool_calls` must be followed by one `tool`
  message per call. Cut between them and the provider rejects the whole request — a routine
  fold becomes a dead turn.
* **A new stable prefix.** The point of folding rather than shaving is that it happens once and
  the result never changes again, so the cache breakpoint on the last message can be read on
  every round afterwards. A fold that leaves anything moving has bought nothing.

What survives verbatim is also deliberate: the system prompt (it is the cached head) and the
first thing his person said. That second one is not sentiment — a summary of the request is
exactly the paraphrase that turned "discover models through the OpenRouter API" into "add model
management" on a real project, and shipped the wrong feature.
"""

from __future__ import annotations

from kith.services import compaction


def _turn(steps: int) -> list[dict]:
    convo: list[dict] = [
        {"role": "system", "content": "persona"},
        {"role": "user", "content": "add a model chooser, pulling models from the OpenRouter API"},
    ]
    for i in range(steps):
        convo.append(
            {"role": "assistant", "tool_calls": [{"id": f"c{i}", "function": {"name": "read_file", "arguments": "{}"}}]}
        )
        convo.append({"role": "tool", "tool_name": "read_file", "content": f"contents {i}"})
    return convo


def _parallel_turn() -> list[dict]:
    """One assistant message calling three tools, so its results arrive as three messages."""
    return [
        {"role": "system", "content": "persona"},
        {"role": "user", "content": "look at three things"},
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "a", "function": {"name": "web_search", "arguments": "{}"}},
                {"id": "b", "function": {"name": "web_search", "arguments": "{}"}},
                {"id": "c", "function": {"name": "web_search", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_name": "web_search", "content": "one"},
        {"role": "tool", "tool_name": "web_search", "content": "two"},
        {"role": "tool", "tool_name": "web_search", "content": "three"},
    ] + _turn(10)[2:]


def _pairs_are_intact(convo: list[dict]) -> bool:
    """Every assistant tool_call is answered before the next assistant turn."""
    pending = 0
    for message in convo:
        if message.get("role") == "assistant" and message.get("tool_calls"):
            if pending:
                return False
            pending = len(message["tool_calls"])
        elif message.get("role") == "tool":
            if pending == 0:
                return False  # an orphaned result
            pending -= 1
    return pending == 0


class TestItNeverBreaksTheConversation:
    def test_the_folded_turn_still_pairs_up(self):
        convo = _turn(30)
        assert compaction.fold(convo, lambda _: "notes") is True
        assert _pairs_are_intact(convo)

    def test_it_pairs_up_with_parallel_tool_calls_too(self):
        """Three results to one call. A cut point that assumed one-to-one would orphan two."""
        convo = _parallel_turn()
        assert compaction.fold(convo, lambda _: "notes") is True
        assert _pairs_are_intact(convo)

    def test_the_cut_never_lands_on_a_tool_result(self):
        for steps in range(8, 40):
            convo = _turn(steps)
            index = compaction.cut_point(convo)
            if index:
                assert convo[index].get("role") != "tool", f"{steps} steps cut onto a result"

    def test_a_short_turn_is_left_alone(self):
        convo = _turn(2)
        before = [dict(m) for m in convo]
        assert compaction.fold(convo, lambda _: "notes") is False
        assert convo == before


class TestWhatSurvives:
    def test_the_system_prompt_is_untouched(self):
        convo = _turn(30)
        compaction.fold(convo, lambda _: "notes")
        assert convo[0] == {"role": "system", "content": "persona"}

    def test_the_original_request_survives_verbatim(self):
        convo = _turn(30)
        compaction.fold(convo, lambda _: "notes")
        assert "pulling models from the OpenRouter API" in convo[1]["content"]

    def test_the_recent_work_survives(self):
        convo = _turn(30)
        tail = [dict(m) for m in convo[-compaction.KEEP_LAST :]]
        compaction.fold(convo, lambda _: "notes")
        assert convo[-len(tail) :] == tail

    def test_the_notes_are_in_there(self):
        convo = _turn(30)
        compaction.fold(convo, lambda _: "the note the model wrote")
        assert any("the note the model wrote" in str(m.get("content") or "") for m in convo)

    def test_it_actually_gets_smaller(self):
        convo = _turn(40)
        before = sum(len(str(m.get("content") or "")) for m in convo)
        compaction.fold(convo, lambda _: "short")
        assert sum(len(str(m.get("content") or "")) for m in convo) < before


class TestTheResultIsStable:
    def test_a_fold_is_byte_identical_on_the_next_round(self):
        """The property the whole design exists for: after a fold, the prefix stops moving."""
        convo = _turn(30)
        compaction.fold(convo, lambda _: "notes")
        after_fold = [dict(m) for m in convo]
        convo.append({"role": "assistant", "content": "carrying on"})
        assert convo[: len(after_fold)] == after_fold

    def test_a_failed_summary_still_leaves_a_stable_note(self):
        """When the model cannot be reached the note must carry no counts. "9 steps" becoming
        "11 steps" next round would invalidate the prefix from that point on every round —
        which is the bug this module exists to avoid."""
        first = _turn(30)
        second = _turn(31)

        def explode(_prompt):
            raise RuntimeError("provider down")

        compaction.fold(first, explode)
        compaction.fold(second, explode)
        note_one = next(m["content"] for m in first if m.get("_folded"))
        note_two = next(m["content"] for m in second if m.get("_folded"))
        assert note_one == note_two
        assert not any(character.isdigit() for character in note_one)

    def test_an_empty_summary_is_treated_as_a_failure(self):
        convo = _turn(30)
        compaction.fold(convo, lambda _: "   ")
        note = next(m["content"] for m in convo if m.get("_folded"))
        assert "could not be written" in note


class TestFoldingMoreThanOnce:
    def test_a_second_fold_absorbs_the_first(self):
        """One dense note rather than a chain of summaries, each further from the work."""
        convo = _turn(30)
        compaction.fold(convo, lambda _: "first notes")
        convo.extend(_turn(20)[2:])
        assert compaction.fold(convo, lambda _: "second notes") is True
        assert compaction.already_folded(convo) == 1

    def test_the_count_is_what_stops_it_folding_for_ever(self):
        convo = _turn(30)
        compaction.fold(convo, lambda _: "notes")
        assert compaction.already_folded(convo) == 1
        # A fold that absorbs the previous one keeps the count at 1, so the guard in the agent
        # loop is really "how many folds are live", which is the number worth capping.
        assert compaction.already_folded(_turn(4)) == 0
