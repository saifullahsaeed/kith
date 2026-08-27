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
                "key": "1a015eaff8309001abc",
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
            folder, {"id": 900, "key": "1a015eaff8309001abc", "goal": "Theirs", "status": "working"}
        )
        board_sync.pull(db, project_id, str(folder))
        theirs = next(t for t in repo.tasks.list_tasks(db) if t["goal"] == "Theirs")
        assert theirs["key"] == "1a015eaff8309001abc"

    def test_its_checklist_comes_with_it(self, shared):
        db, project_id, folder, _ = shared
        project_files.write_brief(
            folder,
            {
                "id": 901,
                "key": "1a015eaff8309002abc",
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
        assert (out["added"], out["updated"], out["kept"]) == ([], [], [])


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
        assert board_sync.pull(db, 1, "")["added"] == []

    def test_a_folder_that_is_not_there_is_a_no_op(self, db: Path, tmp_path: Path):
        assert board_sync.pull(db, 1, str(tmp_path / "gone"))["added"] == []


class TestLookingBeforeTakingItIn:
    """`preview` is the default and `pull` is what you reach for once you have looked.

    An import that runs on its own and gets something wrong is expensive to unwind; a report that
    gets something wrong costs a sentence. Same shape as `project_files.neutered_by`.
    """

    def test_it_says_what_would_come_in(self, shared):
        db, project_id, folder, _ = shared
        project_files.write_brief(
            folder, {"id": 900, "key": "1a015eaff8309001abc", "goal": "Theirs", "status": "working"}
        )
        assert board_sync.preview(db, project_id, str(folder))["added"] == ["Theirs"]

    def test_and_changes_nothing(self, shared):
        db, project_id, folder, task = shared
        project_files.write_brief(
            folder, {"id": 900, "key": "1a015eaff8309001abc", "goal": "Theirs", "status": "working"}
        )
        project_files.write_brief(
            folder, {**task, "status": "done", "updated_at": "2099-01-01T00:00:00+00:00"}
        )
        before = {t["id"]: dict(t) for t in repo.tasks.list_tasks(db)}
        board_sync.preview(db, project_id, str(folder))
        assert {t["id"]: dict(t) for t in repo.tasks.list_tasks(db)} == before

    def test_it_reports_exactly_what_pulling_then_does(self, shared):
        """One traversal behind both. Two would drift, and the one that drifted would be the
        report — so the thing you looked at would stop being the thing that happened."""
        db, project_id, folder, task = shared
        project_files.write_brief(
            folder, {"id": 900, "key": "1a015eaff8309001abc", "goal": "Theirs", "status": "working"}
        )
        project_files.write_brief(
            folder, {**task, "status": "done", "updated_at": "2099-01-01T00:00:00+00:00"}
        )
        looked = board_sync.preview(db, project_id, str(folder))
        happened = board_sync.pull(db, project_id, str(folder))
        assert (looked["added"], looked["updated"], looked["kept"]) == (
            happened["added"],
            happened["updated"],
            happened["kept"],
        )

    def test_it_says_how_long_the_folder_has_been_quiet(self, shared):
        """Git is not a sync daemon. "Nothing came in" and "nobody has fetched" are the same
        silence and mean opposite things."""
        db, project_id, folder, _ = shared
        assert board_sync.preview(db, project_id, str(folder))["changed_ago"] >= 0


class TestAFolderItMustNotRead:
    def test_a_brief_with_conflict_markers_stops_everything(self, shared):
        """The worst outcome available here. `read_brief` is forgiving by design, so it would
        take the first `**Status:**` it found and import one side — silently, and the conflict
        would be resolved by having been read, with the losing side gone before anyone saw
        there was a disagreement."""
        db, project_id, folder, _ = shared
        (folder / ".kith" / "tasks" / "99-theirs.md").write_text(
            "# Theirs\n<<<<<<< HEAD\n**Status:** done\n=======\n**Status:** working\n>>>>>>> origin/main\n"
        )
        out = board_sync.pull(db, project_id, str(folder))
        assert "conflict markers" in out["blocked"]
        assert out["added"] == [] and out["updated"] == []

    def test_it_refuses_the_whole_folder_rather_than_skipping_the_file(self, shared):
        """A folder mid-merge is not partly trustworthy."""
        db, project_id, folder, _ = shared
        project_files.write_brief(
            folder, {"id": 900, "key": "1a015eaff8309001abc", "goal": "Fine on its own", "status": "working"}
        )
        (folder / ".kith" / "tasks" / "99-theirs.md").write_text("# T\n<<<<<<< HEAD\nx\n")
        assert board_sync.pull(db, project_id, str(folder))["added"] == []
        assert "Fine on its own" not in [t["goal"] for t in repo.tasks.list_tasks(db)]

    def test_a_repository_mid_merge_stops_it_too(self, shared):
        db, project_id, folder, _ = shared
        (folder / ".git").mkdir(parents=True, exist_ok=True)
        (folder / ".git" / "MERGE_HEAD").write_text("abc123\n")
        assert "middle of something" in board_sync.pull(db, project_id, str(folder))["blocked"]

    def test_it_names_the_file_rather_than_saying_no(self, shared):
        """The caller is a turn and the person needs to know which file to go and look at."""
        db, project_id, folder, _ = shared
        (folder / ".kith" / "tasks" / "99-theirs.md").write_text("# T\n<<<<<<< HEAD\nx\n")
        assert "99-theirs.md" in board_sync.pull(db, project_id, str(folder))["blocked"]


class TestWhereItIsSaid:
    """Attached to `list_tasks`, which is the one moment it matters and is not a hot path.

    `project_binding.adopt` was the other candidate and is the wrong one: it fires on every
    *write*, so an import there would run inside the write that triggered it, changing the board
    underneath the turn doing the changing.
    """

    def test_it_says_nothing_when_the_folder_agrees(self, shared, monkeypatch):
        db, project_id, _folder, _ = shared
        monkeypatch.setattr(board_sync.session_context, "current_project", lambda: project_id)
        assert board_sync.waiting_here(db) == ""

    def test_it_names_what_is_waiting(self, shared, monkeypatch):
        db, project_id, folder, _ = shared
        monkeypatch.setattr(board_sync.session_context, "current_project", lambda: project_id)
        project_files.write_brief(
            folder,
            {"id": 900, "key": "1a015eaff8309001abc", "goal": "Theirs", "status": "working"},
        )
        said = board_sync.waiting_here(db)
        assert "Theirs" in said
        assert "not on this board" in said

    def test_it_says_when_the_folder_cannot_be_trusted(self, shared, monkeypatch):
        db, project_id, folder, _ = shared
        monkeypatch.setattr(board_sync.session_context, "current_project", lambda: project_id)
        (folder / ".kith" / "tasks" / "99-theirs.md").write_text("# T\n<<<<<<< HEAD\nx\n")
        said = board_sync.waiting_here(db)
        assert "cannot be read right now" in said and "Nothing has been taken" in said

    def test_it_reminds_you_that_nothing_arrives_on_its_own(self, shared, monkeypatch):
        """Git is not a sync daemon, and silence from an unfetched remote looks exactly like
        agreement."""
        db, project_id, folder, _ = shared
        monkeypatch.setattr(board_sync.session_context, "current_project", lambda: project_id)
        project_files.write_brief(
            folder, {"id": 900, "key": "1a015eaff8309001abc", "goal": "Theirs", "status": "working"}
        )
        assert "check_remote" in board_sync.waiting_here(db)

    def test_it_still_only_says(self, shared, monkeypatch):
        """`pull` is called by nobody. This is the sentence that lets a person decide."""
        db, project_id, folder, _ = shared
        monkeypatch.setattr(board_sync.session_context, "current_project", lambda: project_id)
        project_files.write_brief(
            folder, {"id": 900, "key": "1a015eaff8309001abc", "goal": "Theirs", "status": "working"}
        )
        before = len(repo.tasks.list_tasks(db))
        board_sync.waiting_here(db)
        assert len(repo.tasks.list_tasks(db)) == before

    def test_a_session_on_no_project_is_asked_nothing(self, db: Path, monkeypatch):
        monkeypatch.setattr(board_sync.session_context, "current_project", lambda: None)
        monkeypatch.setattr(board_sync.session_context, "current", lambda: "")
        assert board_sync.waiting_here(db) == ""

    def test_a_failure_reading_the_folder_does_not_fail_the_task_list(self, shared, monkeypatch):
        """Reading somebody else's folder is a courtesy; a task list that failed because of one
        would not be."""
        db, project_id, _, _ = shared
        monkeypatch.setattr(board_sync.session_context, "current_project", lambda: project_id)
        monkeypatch.setattr(board_sync, "preview", lambda *a, **k: 1 / 0)
        assert board_sync.waiting_here(db) == ""


class TestWhatIsNotWorthSaying:
    """A brief older than the row it describes is not an inbox.

    This sentence moved out of `list_tasks` and into the system prompt, where it is read on
    every turn of a project rather than when somebody asks. `kept` — the cases where the board
    is newer — is mirroring lag: nothing to take in, nothing to decide, and permanent. On one
    real project there are seven, so every turn opened with "somebody else's work is in `.kith/`
    and has not been taken in" followed by a list of things that must not be taken in.
    """

    def test_a_stale_brief_alone_says_nothing(self, shared, monkeypatch):
        db, project_id, folder, task = shared
        monkeypatch.setattr(board_sync.session_context, "current_project", lambda: project_id)
        # The brief as it was, and then the row moves on. The file is now the older of the two.
        project_files.write_brief(
            folder, {**task, "status": "planning", "updated_at": "2020-01-01T00:00:00+00:00"}
        )
        repo.tasks.update_task(db, int(task["id"]), status="working")

        assert board_sync.waiting_here(db) == ""

    def test_it_is_still_mentioned_when_something_is_genuinely_waiting(self, shared, monkeypatch):
        db, project_id, folder, task = shared
        monkeypatch.setattr(board_sync.session_context, "current_project", lambda: project_id)
        project_files.write_brief(
            folder, {**task, "status": "planning", "updated_at": "2020-01-01T00:00:00+00:00"}
        )
        repo.tasks.update_task(db, int(task["id"]), status="working")
        project_files.write_brief(
            folder, {"id": 900, "key": "1a015eaff8309001abc", "goal": "Theirs", "status": "working"}
        )

        said = board_sync.waiting_here(db)

        assert "Theirs" in said
        assert "would be left alone" in said
