"""Clicking a filename he wrote in a sentence.

From a real conversation. He was asked which spreadsheet he had used and answered, correctly:

    1. First file: `Staff.xlsx`
       Path: /Users/saifullahsaeed/Kith/inbox/Staff.xlsx
    2. Second (final) file: `Staff-SAIF.xlsx`
       Path: /Users/saifullahsaeed/Kith/inbox/Staff-SAIF.xlsx

Both files existed, exactly where he said. Clicking the name opened the viewer on
`Staff-SAIF.xlsx` — anchored at the workspace root, because a bare name has nothing else to
be anchored to — and it said **there's no Staff-SAIF.xlsx** about a file two clicks away in
Finder. The full path beside it was not clickable at all, so the only thing you could click
was the only one that could not be found.

Nothing here was a bug in the ordinary sense. `resolve` did what it is for, the viewer
reported what it was told, and the interface had no way to know that the string was a *name*
rather than a *path*. So it is given one: reads look for the file, writes never do.

The asymmetry is the whole design. A `write_file("Staff.xlsx")` that quietly retargeted a
Staff.xlsx three folders away would be data loss wearing a convenience feature's clothes.
"""

from __future__ import annotations

import pytest

from kith.infra import permissions
from kith.infra import workspace as ws
from kith.infra.workspace import paths


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """His folder, with work in subfolders — and a linked project outside it."""
    home = tmp_path / "Kith"
    project = tmp_path / "ai-play"
    (home / "inbox").mkdir(parents=True)
    (home / "work").mkdir()
    (project / "src").mkdir(parents=True)
    (home / "inbox" / "Staff-SAIF.xlsx").write_bytes(b"PK\x03\x04")
    (home / "work" / "plan.md").write_text("the plan")
    (home / "top.md").write_text("at the root")
    (project / "src" / "main.py").write_text("print()")
    monkeypatch.setattr(paths, "configured_root", lambda: home)
    monkeypatch.setattr(permissions, "linked_project_roots", lambda: (project,))
    return home, project


class TestTheNameIsEnough:
    def test_a_bare_filename_finds_the_file_in_its_subfolder(self, workspace):
        """The failure in the screenshot, in one line."""
        home, _ = workspace
        assert ws.locate("Staff-SAIF.xlsx") == str(home / "inbox" / "Staff-SAIF.xlsx")

    def test_a_path_that_is_already_right_is_left_alone(self, workspace):
        """No search runs for a path that exists. This is the overwhelmingly common case and
        it must stay a single `exists()` — the walk is the exception, not the mechanism."""
        home, _ = workspace
        assert ws.locate("work/plan.md") == str(home / "work" / "plan.md")

    def test_an_absolute_path_he_printed_is_honoured(self, workspace):
        home, _ = workspace
        target = home / "inbox" / "Staff-SAIF.xlsx"
        assert ws.locate(str(target)) == str(target)

    def test_the_sandbox_home_still_means_his_folder(self, workspace):
        """`/home/kith` appears in his own memory rows and in old task files, so it cannot
        stop meaning anything — see `resolve`. Going through `locate` must not lose that."""
        home, _ = workspace
        assert ws.locate("/home/kith/top.md") == str(home / "top.md")

    def test_a_file_in_a_linked_project_is_found_too(self, workspace):
        """Permission already says a linked folder is his. Looking for a file in his folder
        and not in yours would be the same split that made `glob` fail 61% of the time."""
        _, project = workspace
        assert ws.locate("main.py") == str(project / "src" / "main.py")


class TestWhenItCannotBeSure:
    def test_two_files_of_the_same_name_are_named_rather_than_guessed_between(self, workspace):
        """Opening the wrong Staff.xlsx silently is worse than asking which one was meant."""
        home, _ = workspace
        (home / "inbox" / "report.md").write_text("a")
        (home / "work" / "report.md").write_text("b")
        with pytest.raises(ws.WorkspaceError) as refused:
            ws.locate("report.md")
        said = str(refused.value)
        assert "more than one" in said
        assert "inbox/report.md" in said and "work/report.md" in said

    def test_the_folders_he_wrote_break_the_tie(self, workspace):
        """`site/report.md` says more than `report.md` does, and both are things he writes.
        The path is still wrong — nothing is at `<root>/site/report.md` — but it is wrong in
        a way that carries information, so it is used rather than thrown away."""
        home, _ = workspace
        (home / "docs" / "site").mkdir(parents=True)
        (home / "docs" / "site" / "report.md").write_text("a")
        (home / "work" / "report.md").write_text("b")
        assert ws.locate("site/report.md") == str(home / "docs" / "site" / "report.md")

    def test_a_name_that_is_nowhere_says_so(self, workspace):
        with pytest.raises(ws.WorkspaceError, match=r"there's no nothing\.md"):
            ws.locate("nothing.md")

    def test_nothing_at_all(self, workspace):
        with pytest.raises(ws.WorkspaceError):
            ws.locate("   ")


class TestWhereItWillNotLook:
    def test_his_own_records_are_not_his_work(self, workspace):
        """A click means "show me my file". `.kith` holds transcripts, and a conversation
        named after a document is not that document."""
        home, _ = workspace
        internal = home / paths.INTERNAL_DIR / "conversations"
        internal.mkdir(parents=True)
        (internal / "hidden.md").write_text("a transcript")
        with pytest.raises(ws.WorkspaceError, match="there's no"):
            ws.locate("hidden.md")

    def test_dependencies_are_not_searched(self, workspace):
        """Otherwise one click walks a node_modules, and every `index.js` is ambiguous."""
        home, _ = workspace
        modules = home / "site" / "node_modules" / "left-pad"
        modules.mkdir(parents=True)
        (modules / "vendored.md").write_text("someone else's")
        with pytest.raises(ws.WorkspaceError, match="there's no"):
            ws.locate("vendored.md")


class TestWritesAreUntouched:
    def test_resolve_still_anchors_rather_than_searches(self, workspace):
        """The guard on the whole idea. `resolve` is what a write goes through, and it must
        keep answering "under the working base" for a name that exists elsewhere — otherwise
        writing `Staff-SAIF.xlsx` overwrites the one in `inbox/` instead of making a new one."""
        home, _ = workspace
        assert ws.resolve("Staff-SAIF.xlsx") == str(home / "Staff-SAIF.xlsx")
