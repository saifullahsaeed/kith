"""`worktrees.close` ends in `rmtree`, so it may only ever remove one of the copies.

On 2026-09-28 a builder whose copy had been stubbed to "" reached `close(Path(""))` — the current
directory — and it deleted the whole server folder from under a test run: the source, the
virtualenv and Kith's data. Every case below must now do nothing at all, and the one real case
must still work.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith.infra.workspace import worktrees


@pytest.fixture
def cwd_with_files(tmp_path: Path, monkeypatch) -> Path:
    here = tmp_path / "somewhere"
    here.mkdir()
    (here / "precious.txt").write_text("keep me")
    monkeypatch.chdir(here)
    return here


class TestNothingButACopyIsRemoved:
    @pytest.mark.parametrize("given", ["", ".", "..", "precious.txt", "./"])
    def test_an_empty_or_relative_path_is_ignored(self, cwd_with_files: Path, given: str):
        worktrees.close(Path(given))
        assert (cwd_with_files / "precious.txt").read_text() == "keep me"

    def test_a_folder_outside_the_copies_is_ignored(self, tmp_path: Path):
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        (outside / "file").write_text("x")
        worktrees.close(outside)
        assert (outside / "file").exists()

    def test_the_copies_folder_itself_is_ignored(self):
        home = worktrees._home()
        home.mkdir(parents=True, exist_ok=True)
        (home / "marker").write_text("x")
        worktrees.close(home)
        assert (home / "marker").exists()

    def test_a_symlink_out_of_the_copies_folder_is_ignored(self, tmp_path: Path):
        home = worktrees._home()
        home.mkdir(parents=True, exist_ok=True)
        target = tmp_path / "target"
        target.mkdir()
        (target / "file").write_text("x")
        (home / "sneaky").symlink_to(target)
        worktrees.close(home / "sneaky")
        assert (target / "file").exists()

    def test_a_real_copy_is_still_removed(self):
        copy = worktrees._home() / "w-real"
        copy.mkdir(parents=True)
        (copy / "file").write_text("x")
        worktrees.close(copy)
        assert not copy.exists()
