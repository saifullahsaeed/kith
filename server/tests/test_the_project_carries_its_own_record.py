"""What Kith produces about a project lives in the project, and travels with it.

Three complaints, one shape: "he is not updating memories consistently, I have to ask him",
"all the related data and task briefs should live in the .kith folder in the project so even
if I push, another user with Kith can work on it", and git being "totally shit".

Before this, a real project's memory file referenced `work/srs-review.md` — a path that for
anyone cloning the repository pointed at nothing, because the review was sitting in
`~/Kith/work/` on one machine. And after `base_dir` was fixed it would have gone to the
project *root* instead: his screenshots beside somebody's `src/`.
"""

from __future__ import annotations

import pytest

from kith.services import project_files


class TestTheFolderInsideAProject:
    def test_it_is_dot_kith_in_the_project(self, tmp_path):
        assert project_files.kith_dir(tmp_path) == tmp_path / ".kith"

    def test_it_explains_itself_to_a_human(self, tmp_path):
        """A committed folder nobody can explain is a folder somebody deletes."""
        project_files.ensure(tmp_path)
        readme = (tmp_path / ".kith" / "README.md").read_text()
        assert "Kith" in readme
        assert "committed on purpose" in readme.replace("\n", " ")

    def test_the_readme_is_not_rewritten_over_an_edited_one(self, tmp_path):
        project_files.ensure(tmp_path)
        (tmp_path / ".kith" / "README.md").write_text("my own words\n")
        project_files.ensure(tmp_path)
        assert (tmp_path / ".kith" / "README.md").read_text() == "my own words\n"

    def test_the_three_folders(self, tmp_path):
        for kind in (project_files.WORK, project_files.TASKS, project_files.SCRATCH):
            assert project_files.folder_for(tmp_path, kind).is_dir()

    def test_an_unknown_folder_is_refused(self, tmp_path):
        with pytest.raises(ValueError):
            project_files.folder_for(tmp_path, "wherever")


class TestABriefIsReadable:
    def task(self, **over):
        base = {
            "id": 7,
            "goal": "Build the authenticated dashboard shell",
            "status": "working",
            "priority": "high",
            "description": "npm run build passes and /dashboard renders when logged in",
            "checklist": [
                {"text": "Inspect the current routes", "done": True},
                {"text": "Protect /dashboard", "done": False},
            ],
            "comments": [
                {"author": "user", "body": "footer should be full width"},
                {"author": "kith", "body": "restructured the panel into three bands"},
            ],
            "deliverables": [{"title": "PortalShell.tsx", "path": "frontend/src/components"}],
        }
        return {**base, **over}

    def test_the_filename_says_what_it_is(self, tmp_path):
        """`07-build-the-authenticated-dashboard-shell.md` tells you; `07.md` makes you open it."""
        path = project_files.write_brief(tmp_path, self.task())
        assert path is not None
        assert path.name.startswith("07-build-the-authenticated")
        assert path.suffix == ".md"

    def test_it_carries_everything_a_stranger_needs(self, tmp_path):
        text = project_files.write_brief(tmp_path, self.task()).read_text()
        assert "Build the authenticated dashboard shell" in text
        assert "npm run build passes" in text, "the definition of done"
        assert "- [x] Inspect the current routes" in text
        assert "- [ ] Protect /dashboard" in text
        assert "footer should be full width" in text, "the person's own note"
        assert "PortalShell.tsx" in text

    def test_it_says_it_is_a_mirror(self, tmp_path):
        """Otherwise someone edits the file and wonders why the board disagrees."""
        text = project_files.write_brief(tmp_path, self.task()).read_text()
        assert "rewritten" in text
        assert "Edit the task in Kith" in text

    def test_rewording_the_goal_does_not_leave_a_duplicate(self, tmp_path):
        """A directory of stale near-duplicates is worse than no directory."""
        project_files.write_brief(tmp_path, self.task())
        project_files.write_brief(tmp_path, self.task(goal="Dashboard shell and routing"))

        files = sorted(p.name for p in project_files.folder_for(tmp_path, "tasks").glob("*.md"))

        assert len(files) == 1
        assert "dashboard-shell-and-routing" in files[0]

    def test_it_is_rewritten_not_appended(self, tmp_path):
        first = project_files.write_brief(tmp_path, self.task()).read_text()
        again = project_files.write_brief(tmp_path, self.task()).read_text()
        assert first == again

    def test_a_task_with_nothing_on_it_still_writes(self, tmp_path):
        path = project_files.write_brief(tmp_path, {"id": 1, "goal": "Something small"})
        assert path is not None and "Something small" in path.read_text()

    def test_rubbish_returns_none_rather_than_raising(self, tmp_path):
        """The board is what matters; this is the copy, and a copy that cannot be written must
        not fail the change that prompted it."""
        assert project_files.write_brief(tmp_path, {}) is None
        assert project_files.write_brief(tmp_path, {"id": "not a number"}) is None


class TestTheBriefFollowsTheBoard:
    @pytest.fixture
    def linked(self, tmp_path, db, monkeypatch):
        from kith.infra import workspace
        from kith.infra.db import repositories as repo

        home = tmp_path / "home"
        home.mkdir()
        project = tmp_path / "the-app"
        project.mkdir()
        monkeypatch.setattr(workspace, "configured_root", lambda: home)
        row = repo.projects.add_project(db, "The App", "an app", directory=str(project))
        return {"db": db, "id": int(row["id"]), "project": project}

    def briefs(self, project):
        folder = project / ".kith" / "tasks"
        return sorted(p.name for p in folder.glob("*.md")) if folder.is_dir() else []

    def test_filing_a_task_writes_its_brief(self, linked):
        from kith.tools import registry

        registry.get("add_task").run(
            linked["db"],
            {
                "goal": "Wire the login form",
                "description": "posting the form hits /auth/login and stores the token",
                "project_id": linked["id"],
            },
        )

        assert any("wire-the-login-form" in name for name in self.briefs(linked["project"]))

    def test_a_comment_refreshes_it(self, linked):
        from kith.tools import registry

        made = registry.get("add_task").run(
            linked["db"],
            {
                "goal": "Wire the login form",
                "description": "posting the form hits /auth/login and stores the token",
                "project_id": linked["id"],
            },
        )
        registry.get("comment_on_task").run(
            linked["db"], {"id": int(made["id"]), "comment": "the proxy port was wrong"}
        )

        brief = next(iter((linked["project"] / ".kith" / "tasks").glob("*.md"))).read_text()
        assert "the proxy port was wrong" in brief

    def test_a_task_with_no_project_writes_nothing(self, linked):
        """A one-off errand belongs to nobody's repository."""
        from kith.tools import registry

        registry.get("add_task").run(linked["db"], {"goal": "Buy milk"})
        assert not any("buy-milk" in name for name in self.briefs(linked["project"]))


class TestNudgingHimToRecordWhatHeLearned:
    def test_editing_code_without_touching_memory_is_noticed(self):
        from kith.services import agent_loop

        assert agent_loop._wrote_memory({"path": "frontend/src/App.tsx"}) is False
        assert agent_loop._wrote_memory({"path": ".kith/memory.md"}) is True

    def test_a_batch_that_includes_memory_counts(self):
        """A batch updating the memory alongside three source files has plainly not
        forgotten it."""
        from kith.services import agent_loop

        assert (
            agent_loop._wrote_memory({"edits": [{"path": "src/a.ts"}, {"path": ".kith/memory.md"}]}) is True
        )

    def test_rubbish_arguments_do_not_raise(self):
        from kith.services import agent_loop

        assert agent_loop._wrote_memory(None) is False
        assert agent_loop._wrote_memory({"edits": ["not a dict"]}) is False

    def test_the_directive_asks_for_knowledge_not_a_summary(self):
        """ "Leave something behind" reads as "file a comment", which he was already doing —
        and a comment is about the task, not about the project."""
        from kith.services import agent_loop

        said = agent_loop._MEMORY_DIRECTIVE
        assert "memory.md" in said
        assert "not a summary" in said
        assert "learned nothing durable" in said, "it has to be refusable, or he invents things"
