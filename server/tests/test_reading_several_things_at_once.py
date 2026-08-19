"""The three tools that do the looking take lists, because asking him to batch did not work.

Measured across every transcript on this machine — 11,291 rounds — 78% to 96% of the rounds
that called a tool called exactly *one*, on every model tried. `persona/35-how-you-spend-a-
round.md` was written on 2026-08-13 to fix precisely that, with the measurement in its own
comment, and it did not move: 77% single-call in the 9,272 rounds before it shipped, 88% in the
2,019 after. One session on an unfamiliar codebase spent 82 rounds making 81 calls — 50 greps,
28 reads — to find one bug, at $1.43, because every round re-sends the whole conversation and
each of those calls bought its own round.

So the instruction was not the lever. The shape was: batching is not a discipline you have to
remember when the parameter is already a list.

Which leaves one hard requirement, and it is what most of this file is about. Old tool calls are
replayed out of the transcript on every resumed turn (`conversations.full_messages`), so the
singular spelling has to keep working forever — otherwise reopening last week's conversation
fails on arguments that were correct when they were made.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith.domain.tooling import many
from kith.tools import code, computer


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch) -> Path:
    """A real folder with real files. These tools go through the sandbox and its permission
    gate, and what is worth testing is what they do with several paths — not a mocked reader
    that would also have mocked away the gate."""
    from kith.infra import workspace

    (tmp_path / "one.py").write_text("def alpha():\n    return 1\n")
    (tmp_path / "two.py").write_text("def beta():\n    return 2\n")
    (tmp_path / "three.py").write_text("def gamma():\n    return 3\n")
    # The root itself, so paths resolve *inside* the workspace and the read gate stays shut for
    # the right reason rather than being switched off. Same fixture the code-reading tests use.
    monkeypatch.setattr(workspace.paths, "configured_root", lambda: tmp_path)
    return tmp_path


class TestTheOldSpellingStillWorks:
    """Not politeness. A resumed conversation replays its own history verbatim."""

    @pytest.mark.parametrize(
        "sent",
        [
            {"paths": ["a", "b"]},  # the declared shape
            {"paths": "a"},  # a bare string into an array parameter, which models send
            {"path": "a"},  # the singular these tools used to take
        ],
    )
    def test_every_spelling_is_read(self, sent):
        assert many(sent, "paths", "path")

    def test_the_declared_shape_keeps_its_order(self):
        assert many({"paths": ["c", "a", "b"]}, "paths", "path") == ["c", "a", "b"]

    def test_nothing_at_all_is_empty_rather_than_a_guess(self):
        assert many({}, "paths", "path") == []
        assert many({"paths": []}, "paths", "path") == []
        assert many({"paths": "   "}, "paths", "path") == []


class TestOneCallReadsSeveralFiles:
    def test_three_files_come_back_under_three_headings(self, workspace: Path):
        out = computer.read_file(Path(), {"paths": ["one.py", "two.py", "three.py"]})

        assert isinstance(out, str), "a batch returns the same shape one file does"
        for name in ("one.py", "two.py", "three.py"):
            assert f"===== {name} =====" in out
        assert "alpha" in out and "beta" in out and "gamma" in out

    def test_one_file_is_untouched_by_any_of_this(self, workspace: Path):
        """The single-file result is what the transcript, the model and the code block in the
        interface already know. A second shape for the same tool would mean each of them
        growing a branch."""
        one = computer.read_file(Path(), {"paths": ["one.py"]})
        old = computer.read_file(Path(), {"path": "one.py"})

        assert one == old
        assert "=====" not in one

    def test_a_bad_path_does_not_lose_the_good_ones(self, workspace: Path):
        out = computer.read_file(Path(), {"paths": ["one.py", "nope.py", "two.py"]})

        assert "alpha" in out and "beta" in out

    @pytest.mark.parametrize("windowing", [{"symbol": "alpha"}, {"offset": 2}, {"limit": 5}])
    def test_a_window_into_one_file_is_refused_for_several(self, workspace: Path, windowing):
        """Applying it to all of them and applying it to the first are both wrong, and both
        wrong in the way that reads as a correct answer."""
        out = computer.read_file(Path(), {"paths": ["one.py", "two.py"], **windowing})

        assert "error" in out
        assert next(iter(windowing)) in out["error"]

    def test_a_window_still_works_on_a_single_file(self, workspace: Path):
        out = computer.read_file(Path(), {"paths": ["one.py"], "limit": 1})

        assert isinstance(out, str)
        assert "error" not in out


class TestOneCallGrepsSeveralPatterns:
    def test_three_patterns_come_back_headed(self, workspace: Path):
        out = computer.grep(Path(), {"patterns": ["alpha", "beta", "gamma"], "path": "."})

        assert isinstance(out, str)
        for pattern in ("alpha", "beta", "gamma"):
            assert f"===== {pattern} =====" in out

    def test_one_pattern_is_untouched(self, workspace: Path):
        out = computer.grep(Path(), {"patterns": ["alpha"], "path": "."})

        assert "=====" not in out

    def test_nothing_to_search_for_says_so(self, workspace: Path):
        assert "error" in computer.grep(Path(), {"patterns": []})


class TestOneCallOutlinesSeveralFiles:
    def test_the_combined_shape_is_the_single_shape(self, workspace: Path):
        """Same reasoning as `read_file`: the panel renders a listing of definitions off
        `outline`, and `path` is a label on it rather than a lookup key."""
        out = code.outline(Path(), {"paths": ["one.py", "two.py"]})

        assert set(out) >= {"path", "language", "definitions", "outline"}
        assert out["path"] == "2 files"
        assert out["definitions"] == 2
        assert "===== one.py =====" in out["outline"]

    def test_one_file_keeps_its_own_name(self, workspace: Path):
        out = code.outline(Path(), {"paths": ["one.py"]})

        assert out["path"] == "one.py"

    def test_a_file_it_cannot_parse_is_named(self, workspace: Path):
        """In a batch, "not a language I can parse" with no file attached is a sentence you
        cannot act on."""
        (workspace / "notes.zzz").write_text("hello")
        out = code.outline(Path(), {"paths": ["one.py", "notes.zzz"]})

        assert "notes.zzz" in out["outline"]


class TestTheManifestNamesEveryFileInABatch:
    def test_a_four_file_read_is_not_recorded_as_one(self):
        """`touched` reads the same argument through the same reader, so the two cannot
        disagree about what a call named — which they would, immediately, if it kept its own
        idea of where a path lives."""
        from kith.services import touched

        assert touched._paths("read_file", {"paths": ["a.py", "b.py", "c.py"]}) == [
            "a.py",
            "b.py",
            "c.py",
        ]
        assert touched._paths("read_file", {"path": "a.py"}) == ["a.py"]
