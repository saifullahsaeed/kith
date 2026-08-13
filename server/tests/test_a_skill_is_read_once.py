"""A skill opened twice in one turn costs a sentence, not the file again.

Measured over the Sadeef project: 78 opens of 11 distinct skills. `frontend-design` eight
times, `webapp-testing` eight, `verification-before-completion` seven — and four separate
turns that opened **eight skills each** before doing any work. At a median 2,171 tokens per
skill that is roughly seventeen thousand tokens of instructions loaded speculatively and then
carried on every remaining round.

The persona says not to: "the reading costs you the context you would have needed
to do the job." That was advice, and advice loses to the pull of being thorough.
"""

from __future__ import annotations

import pytest

from kith.kernel import session_context
from kith.tools import registry


@pytest.fixture
def a_skill(tmp_path, monkeypatch):
    from kith.services import skills

    folder = tmp_path / "frontend-design"
    folder.mkdir()
    (folder / "SKILL.md").write_text(
        "---\nname: frontend-design\ndescription: making things look intentional\n---\n\n"
        + "Real instructions here. " * 200
    )
    monkeypatch.setattr(skills, "root", lambda: tmp_path)
    return "frontend-design"


def read(name: str):
    return registry.get("read_skill").run(__import__("pathlib").Path("x.db"), {"name": name})


class TestTheSecondReadInOneTurn:
    def test_it_returns_a_sentence_instead_of_the_file(self, a_skill):
        with session_context.a_turn():
            first = read(a_skill)
            second = read(a_skill)

        assert len(str(first)) > 1_000, "the first read must be the real thing"
        assert len(str(second)) < 300, "the second read paid for the file again"
        assert "already opened" in second["note"]

    def test_a_new_turn_may_read_it_again(self, a_skill):
        """Across turns it is genuinely gone — the instructions are not in the new prompt."""
        with session_context.a_turn():
            read(a_skill)
        with session_context.a_turn():
            again = read(a_skill)

        assert len(str(again)) > 1_000

    def test_outside_a_turn_nothing_is_remembered(self, a_skill):
        """A tool called from a test or a script has no turn, and must still work."""
        first = read(a_skill)
        second = read(a_skill)
        assert len(str(first)) > 1_000 and len(str(second)) > 1_000


class TestOpeningTooMany:
    @pytest.fixture
    def several(self, tmp_path, monkeypatch):
        from kith.services import skills

        for name in ("one", "two", "three", "four", "five"):
            folder = tmp_path / name
            folder.mkdir()
            (folder / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: a thing\n---\n\ninstructions " * 20
            )
        monkeypatch.setattr(skills, "root", lambda: tmp_path)

    def test_the_first_few_pass_without_comment(self, several):
        with session_context.a_turn():
            for name in ("one", "two", "three"):
                assert "opened in this one turn" not in str(read(name).get("note") or "")

    def test_past_that_it_says_what_it_is_costing(self, several):
        with session_context.a_turn():
            for name in ("one", "two", "three"):
                read(name)
            fourth = read("four")

        assert "4 skills opened in this one turn" in fourth["note"]
        assert "one, three, two" in fourth["note"] or "four" in fourth["note"]

    def test_it_still_hands_over_the_skill(self, several):
        """A nudge, not a refusal — a job can legitimately need several."""
        with session_context.a_turn():
            for name in ("one", "two", "three"):
                read(name)
            fourth = read("four")

        assert "instructions" in str(fourth)

    def test_a_skill_that_does_not_exist_is_not_counted(self, several):
        """A failed read raises before anything is recorded, so a typo does not eat the
        allowance — which would be the tally punishing him for the one thing it should not."""
        from kith.services.skills import SkillError

        with session_context.a_turn():
            with pytest.raises(SkillError):
                read("nonexistent")
            read("one")
            read("two")
            third = read("three")

        assert "opened in this one turn" not in str(third.get("note") or "")


class TestTheTurnBoundary:
    def test_the_loop_opens_one(self):
        """`stream_agent` wraps the loop so a tool can see the turn at all."""
        import inspect

        from kith.services import agent_loop

        assert "session_context.a_turn()" in inspect.getsource(agent_loop.stream_agent)

    def test_notes_are_empty_outside_a_turn(self):
        assert session_context.turn_notes() == {}

    def test_notes_do_not_leak_between_turns(self):
        with session_context.a_turn():
            session_context.turn_notes()["x"] = 1
        with session_context.a_turn():
            assert session_context.turn_notes() == {}
