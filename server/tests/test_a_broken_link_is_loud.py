"""A project linked to a folder that is not there says so.

Found by asking a plain question — "what's in the shared memory now?" — and discovering
3,068 characters of real project memory sitting unread on disk while every tick was told
the project had none.

The project was linked to `~/Kith/job/sadeef-client-portal`, which had been deleted. The
work was at `~/Desktop/job/sadeef-client-portal`. Nothing anywhere complained: `base_dir()`
falls back to his own folder without a word, and `project_memory.block()` reported "no
memory yet" — so the state was indistinguishable from a fresh project, and the suggested
next step was to write a *new* memory file, which would have buried the real one for good.

The visible symptom was somewhere else entirely: every command he wrote began with
`cd ~/Desktop/job/...`, because he was compensating by hand for a working directory that
was never right.
"""

from __future__ import annotations

import pytest

from kith.services import project_memory


@pytest.fixture
def workspace_root(tmp_path, monkeypatch):
    from kith.infra import workspace

    home = tmp_path / "kith-home"
    home.mkdir()
    monkeypatch.setattr(workspace.paths, "configured_root", lambda: home)
    return home


class TestAFolderThatIsNotThere:
    def test_it_is_not_reported_as_an_empty_memory(self, tmp_path):
        """The two states call for opposite actions: one says write a memory, the other says
        do not write anything until the link is fixed."""
        missing = tmp_path / "gone"

        message = project_memory.block(missing, "My App")

        assert "not there" in message
        assert str(missing) in message, "it has to name the path, or it is unfixable"

    def test_it_tells_him_not_to_start_a_fresh_one(self, tmp_path):
        """The dangerous half. Inviting a new memory file on a broken link is how the real
        one gets buried."""
        message = project_memory.block(tmp_path / "gone", "My App")
        assert "Do not start" in message or "do not start" in message
        assert "link_folder" in message

    def test_an_existing_folder_with_no_memory_still_invites_one(self, tmp_path):
        folder = tmp_path / "real"
        folder.mkdir()

        message = project_memory.block(folder, "My App")

        assert "no `.kith/memory.md` yet" in message
        assert "not there" not in message

    def test_a_folder_with_memory_reads_it(self, tmp_path):
        folder = tmp_path / "real"
        folder.mkdir()
        project_memory.ensure(folder)
        project_memory.path_for(folder).write_text("# Notes\n\nThe tests run with `make test`.\n")

        message = project_memory.block(folder, "My App")

        assert "make test" in message
        assert "not there" not in message


class TestLinkingToSomethingThatDoesNotExist:
    def test_a_relative_path_with_nothing_there_is_refused(self, workspace_root, db):
        """`job/the-app` resolves against *his* folder, so pointing at a codebase on the
        Desktop quietly created an empty `~/Kith/job/the-app` and linked that instead."""
        from kith.infra.db import repositories as repo
        from kith.tools import registry

        project = repo.projects.add_project(db, "The App", "an app")

        with pytest.raises(ValueError) as caught:
            registry.get("link_folder").run(db, {"id": int(project["id"]), "folder": "job/the-app"})

        assert "relative" in str(caught.value)
        assert not (workspace_root / "job" / "the-app").exists(), "it created the folder anyway"

    def test_an_absolute_path_may_still_create_the_folder(self, workspace_root, db, tmp_path):
        """ "A folder you are about to fill" is a real use. It just has to say where."""
        from kith.infra.db import repositories as repo
        from kith.tools import registry

        project = repo.projects.add_project(db, "The App", "an app")
        wanted = tmp_path / "brand-new"

        result = registry.get("link_folder").run(db, {"id": int(project["id"]), "folder": str(wanted)})

        assert wanted.is_dir()
        assert result["directory"] == str(wanted)

    def test_a_relative_path_that_does_exist_is_fine(self, workspace_root, db):
        """Only the *missing* relative path is suspicious — an existing one is unambiguous."""
        from kith.infra.db import repositories as repo
        from kith.tools import registry

        (workspace_root / "already-here").mkdir()
        project = repo.projects.add_project(db, "The App", "an app")

        result = registry.get("link_folder").run(db, {"id": int(project["id"]), "folder": "already-here"})

        assert result["directory"] == str(workspace_root / "already-here")

    def test_linking_to_a_real_codebase_still_works(self, workspace_root, db, tmp_path):
        from kith.infra.db import repositories as repo
        from kith.tools import registry

        codebase = tmp_path / "Desktop" / "the-app"
        codebase.mkdir(parents=True)
        (codebase / "main.py").write_text("print('hi')\n")
        project = repo.projects.add_project(db, "The App", "an app")

        result = registry.get("link_folder").run(db, {"id": int(project["id"]), "folder": str(codebase)})

        assert result["directory"] == str(codebase)
        assert project_memory.path_for(codebase).is_file(), "it should seed the memory file"
