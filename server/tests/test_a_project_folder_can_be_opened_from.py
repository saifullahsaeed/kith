"""A file the page will show you is a file the page will open.

The deliverables page could name a `.docx`, report its size, and download it — and then refuse to
open the same file in Word, listing three folders it was never in:

    Only things under /Users/…/Kith, /Users/…/server/data, /Users/…/server/persona
    can be opened from here.

A project lives wherever its `directory` says, which is the whole point of the field, and
everything a task produces is stored relative to it —
`docs/Sadeef_V2_Comprehensive_Architecture_Specification.docx` is anchored to the project, not to
the workspace root. `_openable_roots` did not know projects existed.

The same shape as the download bug in the commit before this one: a path anchored one way and
checked another. That one resolved against the global root and saved the path as a text file; this
one resolved correctly and then refused what it had resolved.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith.services import handoff


@pytest.fixture
def project_folder(tmp_path, db, monkeypatch):
    """A project with a folder of its own, outside every other root."""
    from kith.infra.db import repositories as repo

    folder = tmp_path / "somewhere-else"
    (folder / "docs").mkdir(parents=True)
    (folder / "docs" / "spec.docx").write_bytes(b"PK\x03\x04 not really a docx")
    repo.projects.add_project(db, "Elsewhere", directory=str(folder))
    monkeypatch.setattr(handoff, "AGENT_DB_PATH", db, raising=False)
    monkeypatch.setattr("kith.settings.AGENT_DB_PATH", db, raising=False)
    return folder


class TestWhatMayBeOpened:
    def test_a_file_in_a_project_folder_is_openable(self, project_folder):
        assert handoff._openable(project_folder / "docs" / "spec.docx")

    def test_the_project_folder_itself_is_a_root(self, project_folder):
        assert project_folder.resolve() in handoff._openable_roots()

    def test_somewhere_that_is_nobody_s_folder_is_still_refused(self, tmp_path, project_folder):
        """The list widening must not become the list disappearing. Handing a path to the
        operating system is a capability, and everything outside the folders Kith owns is still
        refused by name."""
        stranger = tmp_path / "not-his" / "thing.docx"
        stranger.parent.mkdir(parents=True)
        stranger.write_text("x")

        with pytest.raises(handoff.HandoffError):
            handoff._openable(stranger)

    def test_a_traversal_out_of_a_project_folder_is_refused(self, tmp_path, project_folder):
        """`resolve()` settles `../` before the comparison, not after — the version of this check
        that stripped and then tested let `/etc/passwd` through."""
        escape = project_folder / "docs" / ".." / ".." / "not-his" / "thing.docx"
        (tmp_path / "not-his").mkdir(parents=True, exist_ok=True)
        (tmp_path / "not-his" / "thing.docx").write_text("x")

        with pytest.raises(handoff.HandoffError):
            handoff._openable(escape)

    def test_a_project_with_no_folder_adds_nothing(self, db, monkeypatch):
        """Most projects have no directory. An empty string must not become the filesystem root,
        which would make everything openable."""
        from kith.infra.db import repositories as repo

        repo.projects.add_project(db, "Folderless")
        monkeypatch.setattr("kith.settings.AGENT_DB_PATH", db, raising=False)

        assert Path("/") not in handoff._openable_roots()
        assert all(str(root).strip() for root in handoff._openable_roots())
