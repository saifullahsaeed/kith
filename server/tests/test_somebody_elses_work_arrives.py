"""Kith A closes a task, pushes; Kith B pulls — and B goes on showing it open.

That is the whole of "multi-user does not work yet". `.kith/tasks/` is committed with the
project, so a second person's briefs already reach this machine through git, and nothing has
ever read them back: the board is a per-machine database and it is what Kith actually reads.

**One direction.** The folder is where work arrives from other people; the board is where this
machine reads. Two writers on one state is the failure this codebase's thin-shell rule exists to
avoid, so this imports and the board's own writes go on mirroring outward as before.

**Newest wins, and a brief with no timestamp never does.** That second half is not a
technicality, it is the case that would have done damage: two briefs in a real folder here still
say `doing` and `review` for tasks the board marked `done` weeks ago — and `review` stopped being
a status at all. They were written before their tasks stopped changing, and "the file is the
truth" applied naively reverts both.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith.infra import project_files
from kith.infra.db import repositories as repo
from kith.services import board_sync


@pytest.fixture
def shared(db: Path, tmp_path: Path):
    """A project with a folder, one task on the board, and its brief already written."""
    folder = tmp_path / "repo"
    folder.mkdir()
    project = repo.projects.add_project(db, "Shared", "", str(folder))
    task = repo.tasks.add_task(db, "Wire the routes", project_id=int(project["id"]))
    project_files.write_brief(folder, {**task, "key": task["key"]})
    return db, int(project["id"]), folder, task


class TestWorkThatArrivesFromSomebodyElse:
    def test_a_brief_with_no_row_becomes_a_task(self, shared):
        """The case this exists for. Somebody else filed it and pushed."""
        db, project_id, folder, _ = shared
        project_files.write_brief(
            folder,
            {
                "id": 900,
                "key": "1a015eaff8309001",
                "goal": "Theirs",
                "status": "working",
                "priority": "high",
                "updated_at": "2026-08-18T12:00:00+00:00",
            },
        )
        out = board_sync.pull(db, project_id, str(folder))
        assert out["added"] == ["Theirs"]
        goals = [t["goal"] for t in repo.tasks.list_tasks(db)]
        assert "Theirs" in goals

    def test_it_keeps_the_name_it_arrived_with(self, shared):
        """The key is the only thing making it the same task on both machines. A local one
        would make it a different task that happens to read alike."""
        db, project_id, folder, _ = shared
        project_files.write_brief(
            folder, {"id": 900, "key": "1a015eaff8309001", "goal": "Theirs", "status": "working"}
        )
        board_sync.pull(db, project_id, str(folder))
        theirs = next(t for t in repo.tasks.list_tasks(db) if t["goal"] == "Theirs")
        assert theirs["key"] == "1a015eaff8309001"

    def test_its_checklist_comes_with_it(self, shared):
        db, project_id, folder, _ = shared
        project_files.write_brief(
            folder,
            {
                "id": 901,
                "key": "1a015eaff8309002",
                "goal": "With steps",
                "status": "working",
                "checklist": [{"text": "first", "done": True}, {"text": "second", "done": False}],
            },
        )
        board_sync.pull(db, project_id, str(folder))
        made = next(t for t in repo.tasks.list_tasks(db) if t["goal"] == "With steps")
        items = repo.tasks.list_checklist(db, int(made["id"]))
        assert [(i["text"], bool(i["done"])) for i in items] == [("first", True), ("second", False)]

    def test_a_brief_with_no_key_is_not_resurrected(self, shared):
        """29 of these in one real folder — 12 from a project that used to own the directory and
        17 for tasks deleted long ago. A task nobody can point at is not a task."""
        db, project_id, folder, _ = shared
        project_files.write_brief(folder, {"id": 41, "goal": "A ghost", "status": "done"})
        out = board_sync.pull(db, project_id, str(folder))
        assert out["added"] == []
        assert "A ghost" not in [t["goal"] for t in repo.tasks.list_tasks(db)]


class TestWhenBothChanged:
    def test_a_newer_brief_wins(self, shared):
        db, project_id, folder, task = shared
        project_files.write_brief(
            folder,
            {**task, "status": "done", "updated_at": "2099-01-01T00:00:00+00:00"},
        )
        out = board_sync.pull(db, project_id, str(folder))
        assert len(out["updated"]) == 1
        assert repo.tasks.task_detail(db, int(task["id"]))["status"] == "done"

    def test_an_older_brief_does_not(self, shared):
        db, project_id, folder, task = shared
        project_files.write_brief(
            folder, {**task, "status": "done", "updated_at": "2000-01-01T00:00:00+00:00"}
        )
        out = board_sync.pull(db, project_id, str(folder))
        assert len(out["kept"]) == 1, "reported, not applied — you should hear that it differed"
        assert repo.tasks.task_detail(db, int(task["id"]))["status"] != "done"

    def test_a_brief_with_no_timestamp_never_wins(self, shared):
        """The two real ones. Written before the line existed, which is exactly the set most
        likely to be stale — and a missing timestamp read as "now" is how an import undoes work."""
        db, project_id, folder, task = shared
        brief = {**task, "status": "done"}
        brief.pop("updated_at", None)
        project_files.write_brief(folder, brief)
        out = board_sync.pull(db, project_id, str(folder))
        assert out["updated"] == []
        assert repo.tasks.task_detail(db, int(task["id"]))["status"] != "done"

    def test_agreement_changes_nothing(self, shared):
        db, project_id, folder, _ = shared
        out = board_sync.pull(db, project_id, str(folder))
        assert out == {"added": [], "updated": [], "kept": []}


class TestItOnlyEverGoesOneWay:
    def test_a_task_with_no_brief_is_not_deleted(self, shared):
        """The board is not corrected to match the folder. A missing brief means nobody wrote
        one, not that somebody deleted the task — and guessing wrong loses work."""
        db, project_id, folder, task = shared
        for doc in (folder / ".kith" / "tasks").glob("*.md"):
            doc.unlink()
        board_sync.pull(db, project_id, str(folder))
        assert repo.tasks.task_detail(db, int(task["id"])) is not None

    def test_a_project_with_no_folder_is_a_no_op(self, db: Path):
        assert board_sync.pull(db, 1, "") == {"added": [], "updated": [], "kept": []}

    def test_a_folder_that_is_not_there_is_a_no_op(self, db: Path, tmp_path: Path):
        assert board_sync.pull(db, 1, str(tmp_path / "gone")) == {"added": [], "updated": [], "kept": []}
