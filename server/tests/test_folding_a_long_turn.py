"""Folding the middle of a turn into notes, and never breaking the conversation to do it.

Two hard constraints, and getting either wrong is worse than not folding at all:

* **Tool pairing.** An assistant message carrying `tool_calls` must be followed by one `tool`
  message per call. Cut between them and the provider rejects the whole request — a routine
  fold becomes a dead turn.
* **A new stable prefix.** The point of folding rather than shaving is that it happens once and
  the result never changes again, so the cache breakpoint on the last message can be read on
  every round afterwards. A fold that leaves anything moving has bought nothing.

What survives verbatim is also deliberate: everything the turn was handed — the system prompt
(it is the cached head) and the request it is answering, and in a conversation the brief and the
recent turns between them. The request is not sentiment — a summary of it is exactly the
paraphrase that turned "discover models through the OpenRouter API" into "add model management"
on a real project, and shipped the wrong feature.
"""

from __future__ import annotations

from kith.services import brief, compaction


def _turn(steps: int) -> list[dict]:
    convo: list[dict] = [
        {"role": "system", "content": "persona"},
        {"role": "user", "content": "add a model chooser, pulling models from the OpenRouter API"},
    ]
    for i in range(steps):
        convo.append(
            {
                "role": "assistant",
                "tool_calls": [{"id": f"c{i}", "function": {"name": "read_file", "arguments": "{}"}}],
            }
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
        *_turn(10)[2:],
    ]


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

    def test_a_failed_summary_leaves_the_turn_as_it_was(self):
        """It used to replace the middle with a note saying the summary could not be written —
        right when this fired at 80% of the window and the room was needed whatever happened. It
        fires at the fold point now, where it is about cost: a turn that has lost the results it
        was working from re-reads them. When room really runs out, dropping exchanges makes it."""
        convo = _turn(30)
        before = [dict(m) for m in convo]

        def explode(_request):
            raise RuntimeError("provider down")

        assert compaction.fold(convo, explode) is False
        assert convo == before

    def test_an_empty_summary_is_treated_as_a_failure(self):
        convo = _turn(30)
        before = [dict(m) for m in convo]
        assert compaction.fold(convo, lambda _: "   ") is False
        assert convo == before


class TestTheRequestIsTheTurnItself:
    def test_it_is_the_turn_up_to_the_cut_with_the_instruction_last(self):
        """The prompt the last round sent, cut where the fold ends — a cache read on the host that
        already holds it — rather than a transcript of the middle, which no host had ever seen."""
        convo = _turn(30)
        seen: list[list[dict]] = []
        end = compaction.cut_point(convo)

        compaction.fold(convo, lambda request: seen.append(request) or "notes")

        request = seen[0]
        assert request[:-1] == _turn(30)[:end]
        assert request[-1]["role"] == "user"
        assert request[-1]["content"].startswith("Stop here.")

    def test_everything_the_turn_was_handed_survives(self):
        """In a conversation the head is the persona, the folded brief, the recent turns, the
        message being answered and the present-state block. The old rule — the system prompt and
        the first user message — kept the first thing ever said in the conversation and folded
        the message the turn was actually answering into prose about it."""
        handed = [
            {"role": "system", "content": "persona"},
            {"role": "system", "content": "[Summary of the earlier part]", "_summary": True},
            {"role": "user", "content": "the first thing ever said"},
            {"role": "assistant", "content": "an old reply"},
            {"role": "user", "content": "add a model chooser, pulling models from the OpenRouter API"},
            {"role": "system", "content": "the clock", "_live": True},
        ]
        convo = [*handed, *_turn(30)[2:]]

        assert compaction.fold(convo, lambda _: "notes") is True

        assert convo[: len(handed)] == handed
        assert convo[len(handed)].get("_folded")

    def test_a_turn_with_nothing_of_its_own_to_fold_is_left_alone(self):
        """A long conversation and a short turn: there is a lot before the head and nothing after
        it worth a call. Asked before announcing, so nobody is told a turn is folding."""
        history = [m for i in range(40) for m in _turn(1)[1:]]
        convo = [
            {"role": "system", "content": "persona"},
            *history,
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "the clock", "_live": True},
            *_turn(3)[2:],
        ]
        assert compaction.can_fold(convo) is False
        assert compaction.fold(convo, lambda _: "notes") is False


def _notes(said: str) -> str:
    return "\n\n".join(f"## {section}\n{said}" for section in brief.SECTIONS)


class TestFoldingMoreThanOnce:
    def test_a_second_fold_takes_the_first_into_its_middle(self):
        """One dense note rather than a chain of summaries, each further from the work."""
        convo = _turn(30)
        compaction.fold(convo, lambda _: "first notes")
        convo.extend(_turn(20)[2:])
        assert compaction.fold(convo, lambda _: "second notes") is True
        notes = [m for m in convo if m.get("_folded")]
        assert len(notes) == 1
        assert "second notes" in notes[0]["content"]

    def test_the_first_notes_lasting_sections_are_kept_word_for_word(self):
        """Summarising the previous summary is how the start of a long turn decays. The second
        fold is told the first notes are kept, asked only for what is new, and the lasting
        sections of the first are carried by the code."""
        convo = _turn(30)
        compaction.fold(convo, lambda _: _notes("from the first stretch"))
        convo.extend(_turn(20)[2:])
        seen: list[list[dict]] = []
        compaction.fold(convo, lambda request: seen.append(request) or _notes("from the second stretch"))

        note = next(m["content"] for m in convo if m.get("_folded"))
        done = note.split("## What was done")[1].split("## What was found")[0]
        assert "from the first stretch" in done and "from the second stretch" in done
        where = note.split("## Where it stands")[1]
        assert "from the second stretch" in where and "from the first stretch" not in where
        assert "ONLY what is new" in seen[0][-1]["content"]
