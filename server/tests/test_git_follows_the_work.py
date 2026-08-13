"""Git happens where the work happens.

`_git` did `here = root()`, so `commit`, `changes` and `history` all ran in his own folder
whatever project he was on. Working in a codebase on the Desktop, `changes` reported an empty
diff, `commit` committed nothing and `history` showed nothing — so across five hours he used
`commit` once and `history` never, which is the correct response to three tools that do
nothing. The repository ended with **no commits at all** against 52 changed files and 2,284
insertions: five hours of work with no point to return to.

The same mistake as `base_dir` itself, one layer along. That fix taught *paths* to follow the
project; nobody checked git.
"""

from __future__ import annotations

import pytest

from kith.infra import workspace
from kith.kernel import session_context


@pytest.fixture
def home_and_project(tmp_path, monkeypatch, db):
    """His own folder, and a separate project with a git repo of its own."""
    from kith.infra.db import repositories as repo

    home = tmp_path / "kith-home"
    home.mkdir()
    project = tmp_path / "Desktop" / "the-app"
    project.mkdir(parents=True)
    monkeypatch.setattr(workspace.paths, "configured_root", lambda: home)
    monkeypatch.setattr("kith.settings.AGENT_DB_PATH", db)

    row = repo.projects.add_project(db, "The App", "an app", directory=str(project))
    return {"home": home, "project": project, "id": int(row["id"])}


class TestWhereGitRuns:
    def test_with_no_project_it_is_his_own_folder(self, home_and_project):
        workspace.ensure_repo()
        assert workspace.git._repo_root(workspace.base_dir()) == home_and_project["home"]

    def test_on_a_project_it_is_the_project(self, home_and_project):
        with session_context.working_on(home_and_project["id"]):
            workspace.ensure_repo()
            assert workspace.git._repo_root(workspace.base_dir()) == home_and_project["project"]

    def test_changes_shows_the_projects_diff_not_an_empty_one(self, home_and_project):
        """The symptom that made the tools look broken: he asked what he had changed, in a
        project he had just changed twelve files in, and was told nothing."""
        project = home_and_project["project"]
        with session_context.working_on(home_and_project["id"]):
            workspace.ensure_repo()
            (project / "app.py").write_text("print('hello')\n")
            workspace.commit_all("first")
            (project / "app.py").write_text("print('goodbye')\n")

            diff = workspace.diff()

        assert "goodbye" in diff
        assert "app.py" in diff

    def test_a_commit_lands_in_the_project(self, home_and_project):
        project = home_and_project["project"]
        with session_context.working_on(home_and_project["id"]):
            workspace.ensure_repo()
            (project / "app.py").write_text("print('hello')\n")
            made = workspace.commit_all("the first real point")

            assert made, "it reported nothing committed"
            assert "the first real point" in workspace.log(3)

        # And his own folder is untouched by it.
        workspace.ensure_repo()
        assert "the first real point" not in workspace.log(5)

    def test_history_is_the_projects_history(self, home_and_project):
        project = home_and_project["project"]
        with session_context.working_on(home_and_project["id"]):
            workspace.ensure_repo()
            (project / "a.py").write_text("x = 1\n")
            workspace.commit_all("added a")
            (project / "b.py").write_text("y = 2\n")
            workspace.commit_all("added b")

            log = workspace.log(10)

        assert "added a" in log and "added b" in log


class TestNotMakingAMessOfSomebodyElsesRepo:
    def test_it_does_not_nest_a_repo_inside_an_existing_one(self, home_and_project):
        """`git init` in a subfolder of a checkout creates a second repository that swallows
        the work silently. Nothing fails; the commits just go where nobody looks."""
        project = home_and_project["project"]
        with session_context.working_on(home_and_project["id"]):
            workspace.ensure_repo()
        inner = project / "frontend" / "src"
        inner.mkdir(parents=True)

        monkeypatched = workspace.base_dir
        try:
            workspace.base_dir = lambda: inner  # type: ignore[assignment]
            workspace.ensure_repo()
        finally:
            workspace.base_dir = monkeypatched  # type: ignore[assignment]

        assert not (inner / ".git").exists(), "it started a second repository"

    def test_it_does_not_write_a_gitignore_into_a_project(self, home_and_project):
        """A project has its own opinion about what to ignore and did not ask for ours — and
        it would be the first thing they saw in the diff."""
        project = home_and_project["project"]
        with session_context.working_on(home_and_project["id"]):
            workspace.ensure_repo()

        assert not (project / ".gitignore").exists()

    def test_it_still_writes_one_in_his_own_folder(self, home_and_project):
        workspace.ensure_repo()
        assert (home_and_project["home"] / ".gitignore").is_file()

    def test_that_gitignore_lets_the_kith_folder_travel(self):
        """The whole folder used to be ignored, which was right when it held only private
        bookkeeping and is wrong now it holds the project's memory and task briefs — those are
        the things that are supposed to reach somebody else's clone."""
        assert ".kith/scratch/" in workspace.git._GITIGNORE
        assert "\n.kith/\n" not in workspace.git._GITIGNORE

    def test_commits_are_authored_as_kith(self, home_and_project):
        """So it is obvious in the log which points he made and which the person did."""
        project = home_and_project["project"]
        with session_context.working_on(home_and_project["id"]):
            workspace.ensure_repo()
            (project / "a.py").write_text("x = 1\n")
            workspace.commit_all("his own work")
            authors = workspace.git._git("log", "--format=%an").output

        assert "Kith" in authors


class TestWhenThereIsNothingToDo:
    def test_committing_with_no_changes_says_so(self, home_and_project):
        with session_context.working_on(home_and_project["id"]):
            workspace.ensure_repo()
            (home_and_project["project"] / "a.py").write_text("x = 1\n")
            workspace.commit_all("first")

            assert workspace.commit_all("nothing new") == ""

    def test_a_machine_with_no_git_is_not_an_error(self, home_and_project, monkeypatch):
        monkeypatch.setattr(workspace.git, "has_git", lambda: False)
        assert workspace.ensure_repo() is False
        assert workspace.commit_all("anything") == ""
