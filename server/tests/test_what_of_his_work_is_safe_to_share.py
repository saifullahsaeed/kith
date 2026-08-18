"""`.kith/` commits with the repository. This is what must not go with it.

The folder is shared on purpose — clone the project and the memory, the briefs and the working
notes come too, which is the whole reason it exists and the reason a second person's Kith can
pick a thread up. So the question is never "may this be committed"; it is "what here must never
be", and the answer has to be written where the folder it governs can be read.

**It was a sentence until now.** `project_files.README` has always told whoever finds the folder
that `scratch/` is "Gitignored", and nothing in this codebase has ever written a gitignore.
Checked against a real project: four scripts from `.kith/scratch/` are committed. They happen to
be clean — and that is luck, not design, because `tools/computer.py` instructs him to put scripts
that touch "a system, someone's account" into precisely that folder.

The second half is the rule that makes the first half moot. Git does not descend into an ignored
directory, so a root-level `.kith/` means `.kith/.gitignore` is never read at all. And it fails
in the direction nobody expects: ignore rules only apply to files git is not already tracking, so
a project that added the rule after the folder existed goes on committing every one of them while
reading as though it commits none. That is live in one project here — line 27, 102 files tracked
straight past it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith.infra import project_files


@pytest.fixture
def project(tmp_path: Path) -> Path:
    project_files.ensure(tmp_path)
    return tmp_path


class TestThePolicyIsWrittenDown:
    def test_ensure_writes_it(self, project: Path):
        assert (project / ".kith" / ".gitignore").is_file()

    def test_the_folder_the_readme_promises_is_ignored_actually_is(self, project: Path):
        """The gap this closes. The README has said "Gitignored" since the folder existed."""
        rules = (project / ".kith" / ".gitignore").read_text()
        assert "scratch/" in rules
        assert "scratch/" in project_files.README or "scratch" in project_files.README

    def test_credentials_are_blocked_by_shape(self, project: Path):
        rules = (project / ".kith" / ".gitignore").read_text()
        for pattern in ("*.env", ".env*", "*.key", "*.pem", "id_rsa*", "*credentials*", "*secret*"):
            assert pattern in rules, pattern

    def test_transcripts_are_blocked_even_though_they_do_not_live_here(self, project: Path):
        """Cheap, and the consequence of being wrong is not. A transcript carries whole tool
        results verbatim: 143 plaintext copies of one API key, measured across two of them."""
        rules = (project / ".kith" / ".gitignore").read_text()
        assert "conversations/" in rules and "offload/" in rules

    def test_it_is_a_denylist_so_new_work_is_shared_by_default(self, project: Path):
        """An allowlist would silently drop the next kind of file somebody invents, and nobody
        would find out until they needed it. The briefs and the notes must not be named here."""
        rules = (project / ".kith" / ".gitignore").read_text()
        assert "tasks" not in rules.replace("# ", "")
        assert "memory.md" not in rules
        assert "!" not in rules, "no negations — a denylist should not need un-saying"

    def test_a_policy_already_there_is_left_alone(self, project: Path):
        """Somebody may have tightened it. Rewriting on every `ensure` would undo that
        silently, and `ensure` runs on ordinary work."""
        rules = project / ".kith" / ".gitignore"
        rules.write_text("theirs/\n")
        project_files.ensure(project)
        assert rules.read_text() == "theirs/\n"


class TestTheRuleThatMakesItAllMoot:
    def test_a_root_rule_for_the_whole_folder_is_reported(self, tmp_path: Path):
        (tmp_path / ".gitignore").write_text("node_modules/\n.kith/\ndist\n")
        assert project_files.neutered_by(tmp_path) == ".kith/"

    @pytest.mark.parametrize("rule", [".kith", ".kith/", "/.kith/", "  .kith/  "])
    def test_however_it_is_written(self, tmp_path: Path, rule: str):
        (tmp_path / ".gitignore").write_text(f"{rule}\n")
        assert project_files.neutered_by(tmp_path)

    def test_a_comment_is_not_a_rule(self, tmp_path: Path):
        (tmp_path / ".gitignore").write_text("# .kith/ was ignored once\nnode_modules/\n")
        assert project_files.neutered_by(tmp_path) == ""

    def test_something_that_merely_starts_the_same_is_not_it(self, tmp_path: Path):
        (tmp_path / ".gitignore").write_text(".kith-backup/\n.kithrc\n")
        assert project_files.neutered_by(tmp_path) == ""

    def test_no_gitignore_at_all_is_not_a_problem(self, tmp_path: Path):
        assert project_files.neutered_by(tmp_path) == ""

    def test_it_is_reported_and_never_repaired(self, tmp_path: Path):
        """Their file. A folder called `.kith` that edits someone's ignore rules on its own is
        exactly the behaviour that gets a folder called `.kith` deleted."""
        (tmp_path / ".gitignore").write_text(".kith/\n")
        project_files.neutered_by(tmp_path)
        project_files.ensure(tmp_path)
        assert (tmp_path / ".gitignore").read_text() == ".kith/\n"
