"""What he knows about one project, kept with that project.

Global memory answers "who is this person". It is the wrong place for "this app's tests run
with `npm test -- --run` and the store mutates before it validates" — that belongs to the
folder, travels with the folder, and dies with the folder.

The decision worth testing is that it is *injected*, not fetched. A file he has to remember
to open is a file he will not open, and that is the shape of nearly every failure this
codebase now carries a comment about: a skill naming a tool he was never given, a handoff
written at the bottom of a file whose top was all he was shown, a roadmap with no order
because ordering was a separate call he never made.
"""

from __future__ import annotations

import pytest

from kith.services import project_memory as pm


@pytest.fixture
def project(tmp_path):
    return tmp_path / "gym-tracker"


class TestTheFileItself:
    def test_it_lives_inside_the_project(self, project):
        assert pm.path_for(project) == project / ".kith" / "memory.md"

    def test_a_project_with_none_reads_as_empty(self, project):
        assert pm.read(project) == ""

    def test_ensure_creates_it_with_headings(self, project):
        made = pm.ensure(project)
        body = made.read_text()
        # Headings, not an empty file. An empty file gets whatever occurred to him first;
        # a scaffolded one gets the things the headings ask for.
        assert "## How to run it" in body
        assert "## Gotchas" in body

    def test_ensure_does_not_clobber_what_is_there(self, project):
        pm.ensure(project)
        pm.path_for(project).write_text("# Project memory\n\nnpm test -- --run\n")
        pm.ensure(project)
        assert "npm test -- --run" in pm.path_for(project).read_text()

    def test_what_he_wrote_comes_back(self, project):
        pm.ensure(project)
        pm.path_for(project).write_text("Tests: `npm test -- --run`. Not `npm test`, it watches.")
        assert "it watches" in pm.read(project)


class TestWhenItGrowsTooLong:
    def test_the_recent_end_is_kept(self, project):
        pm.ensure(project)
        body = "\n".join(f"fact {n}" for n in range(4000))
        pm.path_for(project).write_text(body)
        out = pm.read(project)

        assert len(out) < len(body)
        # The newest part, because memory is appended to as it is learned — the same reasoning
        # as the tick handoff, which was being cut from the wrong end for exactly this reason.
        assert "fact 3999" in out
        assert "fact 0\n" not in out

    def test_it_says_how_much_was_left_out_and_where(self, project):
        pm.ensure(project)
        pm.path_for(project).write_text("x" * (pm.MAX_CHARS + 5_000))
        out = pm.read(project)
        assert "earlier characters are still in" in out
        assert ".kith/memory.md" in out

    def test_it_stays_small_enough_to_send_every_time(self):
        # It is prepended to every request touching the project, so it is paid for on every
        # round. A hundred lines of everything-he-noticed is worse than ten of what mattered.
        assert pm.MAX_CHARS <= 8_000


class TestTheBlockThatReachesThePrompt:
    def test_it_names_the_file_so_he_can_add_to_it(self, project):
        pm.ensure(project)
        pm.path_for(project).write_text("the API is at :8611")
        block = pm.block(project, "Gym Tracker")
        assert ".kith/memory.md" in block
        assert "the API is at :8611" in block
        assert "Gym Tracker" in block

    def test_it_asks_him_to_keep_it_true(self, project):
        pm.ensure(project)
        pm.path_for(project).write_text("something")
        # Stale project memory is worse than none: it is confidently wrong about the code.
        assert "wrong" in pm.block(project).lower()

    def test_an_empty_project_still_gets_told_to_start_one(self, project):
        block = pm.block(project, "Gym Tracker")
        # Silence here would mean the file only ever exists if he invents the idea himself.
        assert ".kith/memory.md" in block
        assert "write" in block.lower()

    def test_a_missing_folder_does_not_raise(self, tmp_path):
        # He may be pointed at a directory that has been moved or deleted. A prompt builder
        # that throws takes the whole turn down with it.
        assert pm.read(tmp_path / "gone") == ""
        assert isinstance(pm.block(tmp_path / "gone"), str)


class TestCreatingAProjectWithAFolder:
    def test_the_directory_is_recorded_and_scaffolded(self, db, tmp_path, monkeypatch):
        from kith.infra import workspace as ws
        from kith.tools import projects as tool

        monkeypatch.setattr(ws.settings, "WORKSPACE_DIR", str(tmp_path), raising=False)
        made = tool.create_project(db, {"name": "Gym Tracker", "directory": "gym-tracker"})

        assert made["directory"] == str(tmp_path / "gym-tracker")
        assert (tmp_path / "gym-tracker" / ".kith" / "memory.md").is_file()
        # And he is told where it is, so the next thing he does can be to write in it.
        assert made["memory"].endswith(".kith/memory.md")

    def test_a_project_without_files_gets_no_pretend_folder(self, db):
        from kith.tools import projects as tool

        made = tool.create_project(db, {"name": "Find a UI designer"})
        # Research and shortlists are real projects with no code. Giving them a directory
        # would put an empty folder and a memory file on disk for nothing.
        assert not made.get("directory")


class TestHisRecordsAreNoLongerInsideHisWork:
    def test_internals_live_beside_the_databases(self, tmp_path, monkeypatch):
        from kith.infra import workspace as ws

        monkeypatch.setattr(ws.settings, "DATA_DIR", str(tmp_path / "data"), raising=False)
        monkeypatch.setattr(ws.settings, "WORKSPACE_DIR", str(tmp_path / "work"), raising=False)
        monkeypatch.setattr(ws, "_migrated", False, raising=False)

        # `.kith` inside a folder means that project's memory now, so it cannot also mean the
        # transcript of every conversation he has ever had.
        assert ws.internal() == tmp_path / "data"
        assert ".kith" not in str(ws.internal())
