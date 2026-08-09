"""Batch edits, and the whitespace-tolerant near-miss.

One edit per round is the wrong unit. A rename across eight call sites is eight rounds, and
a tick only gets sixteen — so the mechanics of a refactor could eat half the budget for
doing it. `edit_files` makes that one round.

The property worth protecting is atomicity. A batch that fails partway must leave the disk
untouched, because a half-applied refactor reported as an error is the worst of both: the
files are wrong AND the caller believes nothing happened.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith.infra import workspace
from kith.infra.workspace import WorkspaceError


@pytest.fixture
def workspace_root(tmp_path, monkeypatch):
    """A real folder on disk that `resolve` treats as his own, so writes are permitted."""
    monkeypatch.setattr(workspace.paths, "configured_root", lambda: tmp_path)
    return tmp_path


def write(root: Path, name: str, text: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class TestApplyingSeveralAtOnce:
    def test_edits_across_several_files_all_land(self, workspace_root):
        write(workspace_root, "a.py", "value = 1\n")
        write(workspace_root, "b.py", "value = 2\n")

        result = workspace.edit_files(
            [
                {"path": "a.py", "old": "value = 1", "new": "value = 10"},
                {"path": "b.py", "old": "value = 2", "new": "value = 20"},
            ]
        )

        assert result["files"] == 2
        assert result["replacements"] == 2
        assert (workspace_root / "a.py").read_text() == "value = 10\n"
        assert (workspace_root / "b.py").read_text() == "value = 20\n"

    def test_several_edits_to_one_file_compose_in_order(self, workspace_root):
        """Applied to the running text, so a later edit sees the earlier one's result."""
        write(workspace_root, "one.py", "first\nsecond\nthird\n")

        workspace.edit_files(
            [
                {"path": "one.py", "old": "first", "new": "1st"},
                {"path": "one.py", "old": "second", "new": "2nd"},
                {"path": "one.py", "old": "third", "new": "3rd"},
            ]
        )

        assert (workspace_root / "one.py").read_text() == "1st\n2nd\n3rd\n"

    def test_an_edit_may_depend_on_one_before_it(self, workspace_root):
        """Composition is not incidental — it is the thing that makes a sequence expressible."""
        write(workspace_root, "chain.py", "alpha\n")

        workspace.edit_files(
            [
                {"path": "chain.py", "old": "alpha", "new": "beta"},
                {"path": "chain.py", "old": "beta", "new": "gamma"},
            ]
        )

        assert (workspace_root / "chain.py").read_text() == "gamma\n"

    def test_the_combined_diff_covers_every_file(self, workspace_root):
        write(workspace_root, "x.py", "old_x\n")
        write(workspace_root, "y.py", "old_y\n")

        result = workspace.edit_files(
            [
                {"path": "x.py", "old": "old_x", "new": "new_x"},
                {"path": "y.py", "old": "old_y", "new": "new_y"},
            ]
        )

        assert "x.py" in result["diff"]
        assert "y.py" in result["diff"]

    def test_replace_all_counts_every_occurrence(self, workspace_root):
        write(workspace_root, "many.py", "n = 1\nn = 1\nn = 1\n")

        result = workspace.edit_files(
            [{"path": "many.py", "old": "n = 1", "new": "n = 2", "replace_all": True}]
        )

        assert result["replacements"] == 3
        assert (workspace_root / "many.py").read_text() == "n = 2\nn = 2\nn = 2\n"


class TestNothingIsWrittenWhenAnythingFails:
    """The atomicity promise, which is the whole reason to prefer this over a loop."""

    def test_a_later_failure_leaves_earlier_files_untouched(self, workspace_root):
        write(workspace_root, "good.py", "keep = 1\n")
        write(workspace_root, "bad.py", "other = 2\n")

        with pytest.raises(WorkspaceError) as caught:
            workspace.edit_files(
                [
                    {"path": "good.py", "old": "keep = 1", "new": "keep = 99"},
                    {"path": "bad.py", "old": "text that is not there", "new": "x"},
                ]
            )

        assert (workspace_root / "good.py").read_text() == "keep = 1\n", "the first edit was written anyway"
        assert (workspace_root / "bad.py").read_text() == "other = 2\n"
        assert "none were applied" in str(caught.value)

    def test_the_message_says_which_edit_failed(self, workspace_root):
        """Six edits go out together; "text not found" alone is unactionable."""
        write(workspace_root, "f.py", "a\nb\n")

        with pytest.raises(WorkspaceError) as caught:
            workspace.edit_files(
                [
                    {"path": "f.py", "old": "a", "new": "A"},
                    {"path": "f.py", "old": "b", "new": "B"},
                    {"path": "f.py", "old": "nope", "new": "C"},
                ]
            )

        assert "edit 3 of 3" in str(caught.value)

    def test_an_edit_whose_target_a_previous_edit_destroyed_fails(self, workspace_root):
        """Rather than silently matching something else."""
        write(workspace_root, "g.py", "target\n")

        with pytest.raises(WorkspaceError) as caught:
            workspace.edit_files(
                [
                    {"path": "g.py", "old": "target", "new": "replaced"},
                    {"path": "g.py", "old": "target", "new": "again"},
                ]
            )

        assert "edit 2" in str(caught.value)
        assert (workspace_root / "g.py").read_text() == "target\n"

    def test_a_missing_file_stops_the_batch(self, workspace_root):
        write(workspace_root, "here.py", "x = 1\n")

        with pytest.raises(WorkspaceError):
            workspace.edit_files(
                [
                    {"path": "here.py", "old": "x = 1", "new": "x = 2"},
                    {"path": "gone.py", "old": "y", "new": "z"},
                ]
            )

        assert (workspace_root / "here.py").read_text() == "x = 1\n"

    def test_ambiguity_still_refuses(self, workspace_root):
        write(workspace_root, "dup.py", "same\nsame\n")

        with pytest.raises(WorkspaceError) as caught:
            workspace.edit_files([{"path": "dup.py", "old": "same", "new": "other"}])

        assert "appears 2 times" in str(caught.value)
        assert (workspace_root / "dup.py").read_text() == "same\nsame\n"

    def test_an_empty_batch_is_refused(self, workspace_root):
        with pytest.raises(WorkspaceError):
            workspace.edit_files([])

    def test_an_edit_with_no_path_is_refused(self, workspace_root):
        with pytest.raises(WorkspaceError) as caught:
            workspace.edit_files([{"old": "a", "new": "b"}])
        assert "no path" in str(caught.value)


class TestMatchingWhenTheIndentationIsWrong:
    """The narrow concession: the model copied the block right and the whitespace wrong.

    Every one of these used to cost a full round to rediscover, and the usual recovery was
    to re-read the file and retry with the same mistake.
    """

    def test_a_block_indented_differently_still_matches(self, workspace_root):
        write(workspace_root, "m.py", "class A:\n    def go(self):\n        return 1\n")

        # Copied without the class-body indentation, as if read out of context.
        result = workspace.edit_file(
            "m.py",
            "def go(self):\n    return 1",
            "def go(self):\n    return 2",
        )

        assert (workspace_root / "m.py").read_text() == "class A:\n    def go(self):\n        return 2\n"
        assert "ignoring indentation" in result

    def test_the_replacement_is_reindented_to_the_files_depth(self, workspace_root):
        """Pasting the new text verbatim would land it at the copy's depth, not the file's."""
        write(workspace_root, "deep.py", "if True:\n        alpha = 1\n        beta = 2\n")

        workspace.edit_file(
            "deep.py",
            "alpha = 1\nbeta = 2",
            "alpha = 10\nbeta = 20",
        )

        assert (workspace_root / "deep.py").read_text() == "if True:\n        alpha = 10\n        beta = 20\n"

    def test_two_candidates_still_refuse(self, workspace_root):
        """Tolerant, not reckless. Ambiguity is the case where guessing is worst."""
        write(workspace_root, "two.py", "def f():\n    x = 1\n\ndef g():\n        x = 1\n")

        with pytest.raises(WorkspaceError) as caught:
            workspace.edit_file("two.py", "def h():\n x = 1", "def h():\n    x = 2")

        assert "not in" in str(caught.value)

    def test_a_single_line_never_takes_the_tolerant_path(self, workspace_root):
        """One stripped line matches far too easily; the failure is an edit on the wrong line."""
        write(workspace_root, "s.py", "        value = 1\n")

        with pytest.raises(WorkspaceError):
            workspace.edit_file("s.py", "value = 1 ", "value = 2")

    def test_an_exact_match_is_never_diverted_to_the_tolerant_path(self, workspace_root):
        """The fast, honest path stays the default and stays silent."""
        write(workspace_root, "e.py", "  a = 1\n  b = 2\n")

        result = workspace.edit_file("e.py", "  a = 1\n  b = 2", "  a = 9\n  b = 8")

        assert "ignoring indentation" not in result
        assert (workspace_root / "e.py").read_text() == "  a = 9\n  b = 8\n"

    def test_a_tolerant_match_inside_a_batch_is_reported(self, workspace_root):
        write(workspace_root, "b.py", "class C:\n    def m(self):\n        return 1\n")

        result = workspace.edit_files(
            [{"path": "b.py", "old": "def m(self):\n    return 1", "new": "def m(self):\n    return 2"}]
        )

        assert "ignoring indentation" in result.get("note", "")


class TestTheToolWrapper:
    def test_it_is_registered_and_runs(self, workspace_root, tmp_path):
        from kith.tools import registry

        write(workspace_root, "t.py", "q = 1\n")
        tool = registry.get("edit_files")
        assert tool is not None

        result = tool.run(
            tmp_path / "agent.db", {"edits": [{"path": "t.py", "old": "q = 1", "new": "q = 2"}]}
        )

        assert result["replacements"] == 1
        assert (workspace_root / "t.py").read_text() == "q = 2\n"

    def test_a_non_list_is_refused_rather_than_crashing(self, workspace_root, tmp_path):
        from kith.tools import registry

        result = registry.get("edit_files").run(tmp_path / "agent.db", {"edits": "not a list"})
        assert "error" in result
