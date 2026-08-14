"""Naming a path that lives in your project rather than in his folder.

`glob` failed 14 times out of 23 over 2026-08-10 to 2026-08-12 — a 61% failure rate on a
tool whose entire job is answering "where are the test files" — and every failure handed the
model a raw Python `ValueError: '…/ai-play/…' is not in the subpath of '/Users/…/Kith'`.

The two halves of the codebase disagreed about where he is allowed to be. `permissions`
knows a folder linked to an active project is his for as long as that project is active, so
the search ran and found things; then the *formatting* used `relative_to(root())`, which
assumes every result lives under his own folder. Permission said yes and presentation
assumed no.

So the fix is a shared way to name a path for him rather than a patched line, because the
assumption is available to make at every call site that formats a path — this one just made
it first.
"""

from __future__ import annotations

import pytest

from kith.infra import permissions
from kith.infra import workspace as ws
from kith.infra.workspace import paths


@pytest.fixture
def two_folders(tmp_path, monkeypatch):
    """His own folder, and a project folder outside it that he has been linked to.

    The linked folder is registered through the same cache `permissions` reads, so the
    permission layer in these tests is the real one and not a stub — that agreement between
    the two layers is the thing under test.
    """
    home = tmp_path / "Kith"
    project = tmp_path / "ai-play"
    (home / "notes").mkdir(parents=True)
    (project / "tests").mkdir(parents=True)
    monkeypatch.setattr(paths, "configured_root", lambda: home)
    monkeypatch.setattr(permissions, "linked_project_roots", lambda: (project,))
    return home, project


class TestNamingAPathForHim:
    def test_a_path_in_his_own_folder_is_named_relative_to_it(self, two_folders):
        home, _ = two_folders
        assert paths.display(home / "notes" / "a.md") == "notes/a.md"

    def test_a_path_in_a_linked_project_is_named_relative_to_that_project(self, two_folders):
        _, project = two_folders
        # Not relative to his own folder, which is what `relative_to(root())` tried to do and
        # crashed on; and not absolute either, because a 200-result listing would then spend
        # most of itself repeating the same prefix.
        assert paths.display(project / "tests" / "test_a.py") == "tests/test_a.py"

    def test_a_path_in_neither_is_named_in_full(self, two_folders, tmp_path):
        # Nothing to be relative *to*, so the honest answer is the whole path. Guessing a
        # relative one would name a file he cannot find.
        elsewhere = tmp_path / "somewhere" / "else.txt"
        assert paths.display(elsewhere) == str(elsewhere)

    def test_his_own_folder_names_itself(self, two_folders):
        home, _ = two_folders
        assert paths.display(home) == "."

    def test_the_innermost_root_wins(self, tmp_path, monkeypatch):
        """A project linked *inside* his folder is named relative to the project, not the folder.

        Both are legitimate answers; the more specific one is the useful one, and picking by
        depth means a caller never has to know which case it is in.
        """
        home = tmp_path / "Kith"
        inner = home / "a-project"
        inner.mkdir(parents=True)
        monkeypatch.setattr(paths, "configured_root", lambda: home)
        monkeypatch.setattr(permissions, "linked_project_roots", lambda: (inner,))
        assert paths.display(inner / "src" / "main.py") == "src/main.py"


class TestGlobbingInALinkedProject:
    def test_it_finds_them_instead_of_crashing(self, two_folders):
        _, project = two_folders
        (project / "tests" / "test_a.py").write_text("")
        (project / "tests" / "test_b.py").write_text("")

        out = ws.glob("test_*.py", str(project))

        assert "test_a.py" in out
        assert "test_b.py" in out

    def test_it_never_leaks_a_python_error_at_him(self, two_folders):
        _, project = two_folders
        (project / "tests" / "test_a.py").write_text("")

        out = ws.glob("test_*.py", str(project))

        # The exact string he was handed 14 times. A raw ValueError is not a message a model
        # can act on, and `glob` has no failure of its own to report here.
        assert "not in the subpath" not in out

    def test_it_says_what_the_paths_are_relative_to(self, two_folders):
        _, project = two_folders
        (project / "tests" / "test_a.py").write_text("")

        out = ws.glob("test_*.py", str(project))

        # Relative paths are worth their brevity only if it is clear what they are relative
        # to. Without this line "tests/test_a.py" could be either folder.
        assert str(project) in out

    def test_his_own_folder_still_works_the_way_it_did(self, two_folders):
        home, _ = two_folders
        (home / "notes" / "test_a.py").write_text("")

        out = ws.glob("test_*.py", str(home))

        assert "notes/test_a.py" in out
