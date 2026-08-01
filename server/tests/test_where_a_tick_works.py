"""A tick works in the project's folder, and knows what that project knows.

Both of these came from one mistake, and it took a person saying "I try working on desktop
and it fails" to find it. The tick asked *"which project am I on?"* exactly once and answered
it from the **conversation** — so an unbound session that picked up a task in a folder-linked
project got neither the folder nor the `.kith/memory.md`, because nobody ever asked what
project the *task* belonged to.

The shape of the bug is worth remembering: linking a folder worked perfectly in chat and did
nothing at all unattended. The case that was broken was the one nobody watches.
"""

from __future__ import annotations

import pytest

from kith.infra import workspace
from kith.services import project_memory, session_context


def _runner_module():
    """The runner *module*, not the singleton that shadows it under the same name.

    `kith.autonomy.runner` as an attribute of its package is the instance, so patching it by
    dotted string reaches the object and fails. `conftest.py` documents the same trap.
    """
    import sys

    return sys.modules["kith.autonomy.runner"]


@pytest.fixture
def linked_project(tmp_path, monkeypatch, db):
    """A project on disk with a linked folder, and a task in it."""
    from kith.infra.db import repositories as repo

    home = tmp_path / "kith-home"
    home.mkdir()
    folder = tmp_path / "Desktop" / "my-app"
    folder.mkdir(parents=True)
    monkeypatch.setattr(workspace, "configured_root", lambda: home)

    project = repo.projects.add_project(db, "My App", "an app", directory=str(folder))
    project_id = int(project["id"])
    return {"db": db, "id": project_id, "folder": folder, "home": home}


class TestWhereRelativePathsLand:
    def test_with_no_project_in_context_it_is_his_own_folder(self, linked_project):
        assert workspace.base_dir() == linked_project["home"]

    def test_a_bound_project_moves_the_working_base(self, linked_project, monkeypatch):
        """The fix, stated plainly: the tick says which project it is on, and paths follow."""
        monkeypatch.setattr("kith.config.AGENT_DB_PATH", linked_project["db"])
        monkeypatch.setattr(workspace, "AGENT_DB_PATH", linked_project["db"], raising=False)

        with session_context.working_on(linked_project["id"]):
            assert workspace.base_dir() == linked_project["folder"]

    def test_it_goes_back_afterwards(self, linked_project, monkeypatch):
        monkeypatch.setattr("kith.config.AGENT_DB_PATH", linked_project["db"])
        with session_context.working_on(linked_project["id"]):
            pass
        assert workspace.base_dir() == linked_project["home"]

    def test_a_project_whose_folder_has_gone_falls_back(self, linked_project, monkeypatch):
        """A linked folder someone deleted or moved must not break every file operation."""
        monkeypatch.setattr("kith.config.AGENT_DB_PATH", linked_project["db"])
        import shutil

        shutil.rmtree(linked_project["folder"])
        with session_context.working_on(linked_project["id"]):
            assert workspace.base_dir() == linked_project["home"]

    def test_nothing_bound_is_not_an_error(self, linked_project):
        with session_context.working_on(None):
            assert workspace.base_dir() == linked_project["home"]


class TestTheProjectATickIsOn:
    """`_project_of`, and the branches that use it."""

    def test_it_reads_the_project_off_a_task(self):
        from kith.autonomy.runner import AutonomyRunner

        assert AutonomyRunner._project_of({"project_id": 7}) == 7

    def test_a_task_with_no_project_is_none(self):
        from kith.autonomy.runner import AutonomyRunner

        assert AutonomyRunner._project_of({"project_id": None}) is None
        assert AutonomyRunner._project_of({}) is None
        assert AutonomyRunner._project_of(None) is None

    def test_rubbish_does_not_raise(self):
        """It runs on every tick; a bad row must not take the step down."""
        from kith.autonomy.runner import AutonomyRunner

        assert AutonomyRunner._project_of({"project_id": "not a number"}) is None


class TestProjectMemoryReachesATick:
    def test_the_block_is_built_from_the_working_project(self, linked_project, monkeypatch):
        """A conversation has been shown `.kith/memory.md` since it existed and a tick never
        was — so everything a project learned about itself was visible while you watched and
        invisible the moment he was on his own."""
        from kith.autonomy.runner import AutonomyRunner

        monkeypatch.setattr("kith.config.AGENT_DB_PATH", linked_project["db"])
        monkeypatch.setattr(_runner_module(), "AGENT_DB_PATH", linked_project["db"])
        project_memory.ensure(linked_project["folder"])
        project_memory.path_for(linked_project["folder"]).write_text(
            "# My App\n\nThe tests run with `npm test -- --run`.\n"
        )

        runner = AutonomyRunner()

        assert "npm test -- --run" in runner._project_memory(linked_project["id"])
        assert runner._project_memory(None) == "", "no project means no block, not a crash"

    def test_a_project_with_no_folder_yields_nothing(self, db, monkeypatch):
        from kith.autonomy.runner import AutonomyRunner
        from kith.infra.db import repositories as repo

        monkeypatch.setattr(_runner_module(), "AGENT_DB_PATH", db)
        project_id = int(repo.projects.add_project(db, "Unlinked", "no folder")["id"])

        assert AutonomyRunner()._project_memory(project_id) == ""


class TestTheContextItself:
    def test_a_project_context_nests_inside_a_conversation(self):
        with session_context.working_in("conv-1"):
            with session_context.working_on(5):
                assert session_context.current() == "conv-1"
                assert session_context.current_project() == 5
            assert session_context.current() == "conv-1"
            assert session_context.current_project() is None

    def test_it_is_empty_outside_any_step(self):
        assert session_context.current_project() is None

    def test_nesting_restores_the_outer_project(self):
        with session_context.working_on(1):
            with session_context.working_on(2):
                assert session_context.current_project() == 2
            assert session_context.current_project() == 1
