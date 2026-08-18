"""Whether the folder can carry the board, asked before anything was bet on it.

`.kith/tasks/` has only ever been written. Whether it could be *read* — whether what is on disk
reconstructs the board rather than merely describing it — had never been tested, and the whole
idea of making the files authoritative rests on the answer.

Run against a real project, the answer was yes, with two surprises:

    database    28 tasks        in the DB but not on disk:   0
    folder      57 briefs       on disk but not in the DB:  29

Nothing on the board was missing from the folder — which is the risk, and it is not there. But
the folder held 29 tasks the board did not: 12 from an earlier project over the same directory,
and **17 that no longer exist anywhere**. `write_brief` deletes a stale *slug* when a goal is
reworded, and nothing has ever deleted a brief because its task was gone.

That is clutter while the database is the board. It is the opposite once the files are: a brief
nobody deletes becomes a task nobody can close. So deletion has to be real before the writer can
be flipped, which is what `forget_brief` is for.

Two briefs also disagreed — `#72` said `doing` where the board said `done`, and `#90` said
`review`, which stopped being a status at all when `waiting` and `review` were removed. Both were
written before their tasks stopped changing, and `_mirror_brief` only fires on a change.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith.infra import project_files


@pytest.fixture
def board(tmp_path: Path) -> Path:
    for task in (
        {"id": 7, "goal": "Build the shell", "status": "working", "priority": "high"},
        {"id": 8, "goal": "Wire the routes", "status": "done", "priority": "normal"},
    ):
        project_files.write_brief(tmp_path, task)
    return tmp_path


class TestABriefReadsBackAsATask:
    def test_the_facts_survive_the_round_trip(self, board: Path):
        read = project_files.read_board(board)
        assert read[7]["goal"] == "Build the shell"
        assert read[7]["status"] == "working"
        assert read[7]["priority"] == "high"

    def test_the_checklist_and_what_is_ticked(self, tmp_path: Path):
        project_files.write_brief(
            tmp_path,
            {
                "id": 9,
                "goal": "g",
                "status": "working",
                "checklist": [{"text": "first", "done": True}, {"text": "second", "done": False}],
            },
        )
        items = project_files.read_board(tmp_path)[9]["checklist"]
        assert items == [{"text": "first", "done": True}, {"text": "second", "done": False}]

    def test_who_filed_it_splits_back_into_two_facts(self, tmp_path: Path):
        project_files.write_brief(
            tmp_path,
            {"id": 10, "goal": "g", "status": "working", "account": "saif@example.com", "created_by": "kith"},
        )
        read = project_files.read_board(tmp_path)[10]
        assert read["account"] == "saif@example.com"
        assert read["created_by"] == "kith"

    def test_a_file_that_is_not_a_brief_is_not_a_task(self, board: Path):
        (board / ".kith" / "tasks" / "notes.md").write_text("# just a note\n")
        assert set(project_files.read_board(board)) == {7, 8}

    def test_a_hand_edited_brief_still_reads(self, board: Path):
        """The folder is theirs once it is committed. An edited heading or a reordered fact line
        must not make a task vanish — only an unreadable id may, and that is a file, not a task."""
        doc = next((board / ".kith" / "tasks").glob("07-*.md"))
        doc.write_text("# Build the shell, actually\n\n**Status:** working\n\nsome notes\n")
        assert project_files.read_board(board)[7]["goal"] == "Build the shell, actually"


class TestDeletionIsReal:
    def test_a_deleted_task_loses_its_brief(self, board: Path):
        assert project_files.forget_brief(board, 7) is True
        assert set(project_files.read_board(board)) == {8}

    def test_deleting_one_that_is_not_there_is_not_an_error(self, board: Path):
        assert project_files.forget_brief(board, 999) is False

    def test_it_finds_the_brief_whatever_the_goal_was_worded_as(self, board: Path):
        """The filename carries a slug of the goal, so the only stable part is the number."""
        doc = next((board / ".kith" / "tasks").glob("07-*.md"))
        doc.rename(doc.parent / "07-something-else-entirely.md")
        assert project_files.forget_brief(board, 7) is True


class TestWhatTheyDisagreeAbout:
    def test_a_ghost_brief_is_reported(self, board: Path):
        """The 17. A task deleted from the board whose file stayed behind."""
        out = project_files.reconcile(board, {8: {"goal": "Wire the routes", "status": "done"}})
        assert out["only_in_files"] == [7]

    def test_a_task_with_no_brief_is_reported(self, board: Path):
        out = project_files.reconcile(
            board, {7: {"status": "working"}, 8: {"status": "done"}, 9: {"status": "planning"}}
        )
        assert out["only_on_board"] == [9]

    def test_a_stale_status_is_reported(self, board: Path):
        out = project_files.reconcile(board, {7: {"status": "done"}, 8: {"status": "done"}})
        assert out["disagree"] == [{"id": 7, "field": "status", "board": "done", "file": "working"}]

    def test_agreement_is_silent(self, board: Path):
        out = project_files.reconcile(
            board,
            {
                7: {"goal": "Build the shell", "status": "working", "priority": "high"},
                8: {"goal": "Wire the routes", "status": "done", "priority": "normal"},
            },
        )
        assert out == {"only_in_files": [], "only_on_board": [], "disagree": []}

    def test_it_corrects_nothing(self, board: Path):
        """Describes, never repairs. Which of the two is right is not a question this can
        answer — it is the question the answer is being gathered for."""
        before = {p.name: p.read_text() for p in (board / ".kith" / "tasks").glob("*.md")}
        project_files.reconcile(board, {7: {"status": "done"}})
        after = {p.name: p.read_text() for p in (board / ".kith" / "tasks").glob("*.md")}
        assert before == after


class TestANameATaskKeepsWhenItLeaves:
    """`tasks.id` is a local handle and cannot be an identity for two machines.

    Not mainly because two people might both reach 108. Because when the second person pulls the
    first one's brief, their Kith writes a local row for it and that row gets *their* next
    number — the same task has a different id on each machine by construction. So the name has to
    come from the file, and `domain/keys` makes one that needs no coordination.

    The 57 briefs already committed in one project here are all filed under numbers, so both
    namings have to work at once, for as long as it takes each task to be touched again.
    """

    def test_a_task_with_a_key_is_filed_under_it(self, tmp_path: Path):
        project_files.write_brief(
            tmp_path, {"id": 8, "goal": "New style", "status": "working", "key": "1a015eaff83fa290"}
        )
        assert (tmp_path / ".kith" / "tasks" / "1a015eaff83fa290-new-style.md").is_file()

    def test_a_task_without_one_keeps_the_name_it_has(self, tmp_path: Path):
        """Not only for old files. Nothing already committed should move because a column
        was added."""
        project_files.write_brief(tmp_path, {"id": 7, "goal": "Old style", "status": "done"})
        assert (tmp_path / ".kith" / "tasks" / "07-old-style.md").is_file()

    def test_both_read_back_at_once(self, tmp_path: Path):
        project_files.write_brief(tmp_path, {"id": 7, "goal": "Old", "status": "done"})
        project_files.write_brief(
            tmp_path, {"id": 8, "goal": "New", "status": "working", "key": "1a015eaff83fa290"}
        )
        assert set(project_files.read_board(tmp_path)) == {7, "1a015eaff83fa290"}

    def test_gaining_a_key_moves_the_brief_rather_than_forking_it(self, tmp_path: Path):
        """The failure this avoids is quiet: one task, two files, and a reader that believes
        there are two tasks."""
        project_files.write_brief(tmp_path, {"id": 7, "goal": "Old style", "status": "done"})
        project_files.write_brief(
            tmp_path, {"id": 7, "goal": "Old style", "status": "done", "key": "1a015eaff8300007"}
        )
        found = sorted(p.name for p in (tmp_path / ".kith" / "tasks").glob("*.md"))
        assert found == ["1a015eaff8300007-old-style.md"]

    def test_reconcile_matches_across_the_two_namings(self, tmp_path: Path):
        """Comparing numbers against keys would report every task twice — once as a ghost and
        once as a gap — which is the exact opposite of what this is for."""
        project_files.write_brief(tmp_path, {"id": 7, "goal": "Before keys", "status": "done"})
        project_files.write_brief(
            tmp_path, {"id": 8, "goal": "After", "status": "working", "key": "1a015eaff83fa290"}
        )
        out = project_files.reconcile(
            tmp_path,
            {
                7: {"goal": "Before keys", "status": "done", "key": "1a015eaff8300007"},
                8: {"goal": "After", "status": "working", "key": "1a015eaff83fa290"},
            },
        )
        assert out == {"only_in_files": [], "only_on_board": [], "disagree": []}

    def test_a_deleted_task_loses_its_brief_under_either_name(self, tmp_path: Path):
        project_files.write_brief(tmp_path, {"id": 7, "goal": "Old", "status": "done"})
        assert project_files.forget_brief(tmp_path, 7, "1a015eaff8300007") is True
        assert project_files.read_board(tmp_path) == {}

    def test_a_file_that_is_hex_but_neither_is_not_a_task(self, tmp_path: Path):
        project_files.write_brief(tmp_path, {"id": 7, "goal": "Real", "status": "done"})
        (tmp_path / ".kith" / "tasks" / "abc-notes.md").write_text("# not a task\n")
        assert set(project_files.read_board(tmp_path)) == {7}
