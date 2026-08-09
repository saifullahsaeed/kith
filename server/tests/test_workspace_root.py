"""Choosing the folder he works in.

The folder is not a preference, it is the security boundary. Inside it he acts without
asking — that is the entire design of :mod:`kith.services.permissions` — so whatever
folder this setting names is, by definition, the region where nothing prompts. Which
makes one mistake catastrophic and silent: picking your home folder does not give him a
roomy workspace, it grants him every file you own with no prompt ever again, and there is
nothing on screen afterwards to show that anything changed.

So the refusals are tested first and hardest, and they are refusals rather than warnings:
a confirmation dialog in front of this would be a dialog people click through.
"""

from __future__ import annotations

import pytest

from kith.infra import workspace


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """A config store of our own, no KITH_WORKSPACE in the way, and a current folder
    under ``tmp_path``.

    That last part matters. Changing the folder copies his records out of the previous
    one, and with nothing stored the previous one is the real ``~/Kith`` — so without
    seeding this, every test here would copy the developer's actual conversations into a
    temporary directory. Correct behaviour, wrong place to exercise it.
    """
    from kith.infra.db import config_store

    db = tmp_path / "config.db"
    config_store.init(db)
    # The module imports kith.config inside the call, so patching the attribute is enough.
    monkeypatch.setattr("kith.config.CONFIG_DB_PATH", db, raising=False)
    monkeypatch.setattr(workspace.paths.settings, "WORKSPACE_DIR", "", raising=False)
    current = tmp_path / "current"
    current.mkdir()
    config_store.update_settings(db, {workspace.ROOT_KEY: str(current)})
    return db


class TestFoldersThatWouldDefeatTheBoundary:
    def test_home_is_refused(self, isolated, tmp_path):
        from pathlib import Path

        with pytest.raises(workspace.WorkspaceError) as caught:
            workspace.set_root(str(Path.home()))
        # The message has to say *why*, because "invalid folder" reads like a bug in the
        # app and sends people back to try it again with a trailing slash.
        assert "too broad" in str(caught.value)

    @pytest.mark.parametrize("name", ["Desktop", "Documents", "Downloads", "Library"])
    def test_the_big_home_folders_are_refused(self, isolated, name):
        from pathlib import Path

        with pytest.raises(workspace.WorkspaceError):
            workspace.set_root(str(Path.home() / name))

    def test_the_filesystem_root_is_refused(self, isolated):
        with pytest.raises(workspace.WorkspaceError):
            workspace.set_root("/")

    def test_a_trailing_slash_does_not_slip_home_past_the_check(self, isolated):
        from pathlib import Path

        # normpath first, then compare: "/Users/you/" and "/Users/you" are one folder, and
        # a set membership test on the raw string would have let one of them through.
        with pytest.raises(workspace.WorkspaceError):
            workspace.set_root(f"{Path.home()}/")

    def test_dot_dot_does_not_slip_home_past_the_check(self, isolated):
        from pathlib import Path

        home = Path.home()
        with pytest.raises(workspace.WorkspaceError):
            workspace.set_root(str(home / "Documents" / ".."))


class TestOrdinaryRefusals:
    def test_empty(self, isolated):
        with pytest.raises(workspace.WorkspaceError):
            workspace.set_root("   ")

    def test_relative(self, isolated):
        with pytest.raises(workspace.WorkspaceError) as caught:
            workspace.set_root("some/folder")
        assert "full path" in str(caught.value)

    def test_a_file_is_not_a_folder(self, isolated, tmp_path):
        target = tmp_path / "notes.txt"
        target.write_text("hi")
        with pytest.raises(workspace.WorkspaceError) as caught:
            workspace.set_root(str(target))
        assert "file, not a folder" in str(caught.value)


class TestChoosingOne:
    def test_it_is_created_and_remembered(self, isolated, tmp_path):
        target = tmp_path / "work" / "kith"
        assert not target.exists()

        chosen = workspace.set_root(str(target))

        assert chosen == target
        assert target.is_dir()
        assert workspace.configured_root() == target
        assert workspace.root() == target

    def test_the_write_probe_leaves_nothing_behind(self, isolated, tmp_path):
        target = tmp_path / "clean"
        workspace.set_root(str(target))
        # A probe file left in his folder would show up in the file browser and in his own
        # listings, and he would reasonably wonder what wrote it.
        assert list(target.iterdir()) == []

    def test_the_environment_still_wins(self, isolated, tmp_path, monkeypatch):
        stored = tmp_path / "picked-in-the-app"
        workspace.set_root(str(stored))
        pinned = tmp_path / "from-the-environment"
        monkeypatch.setattr(workspace.paths.settings, "WORKSPACE_DIR", str(pinned), raising=False)

        # Someone who launched the process pointing at a folder meant it, and a stored
        # value quietly overriding them is how you get a support thread about a setting
        # that "does nothing".
        assert workspace.configured_root() == pinned

    def test_nothing_is_moved(self, isolated, tmp_path):
        first = tmp_path / "one"
        first.mkdir()
        (first / "report.md").write_text("his work")
        workspace.set_root(str(first))

        workspace.set_root(str(tmp_path / "two"))

        # The old folder is untouched. This is the documented behaviour and the interface
        # says so before the change; a settings row must not relocate gigabytes on a click.
        assert (first / "report.md").read_text() == "his work"


class TestFallingBack:
    def test_no_config_database_means_the_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr(workspace.paths.settings, "WORKSPACE_DIR", "", raising=False)
        monkeypatch.setattr("kith.config.CONFIG_DB_PATH", tmp_path / "absent.db", raising=False)
        # First run, or a migration in flight. Answering with the default beats taking the
        # whole app down over a folder setting.
        assert workspace.configured_root() == workspace.DEFAULT_ROOT


class TestHisRecordsComeWithHim:
    """The one thing that must not stay behind.

    Transcripts live in ``.kith/`` under the workspace; the index that lists them lives in
    the databases, which do not move with it. Change folders without carrying the records
    and every past conversation is still listed and every one opens empty — no error, no
    deletion, just history that silently cannot be read.
    """

    def test_conversations_are_copied(self, isolated, tmp_path):
        first = tmp_path / "one"
        (first / workspace.INTERNAL_DIR / "conversations").mkdir(parents=True)
        transcript = first / workspace.INTERNAL_DIR / "conversations" / "20260730-1.jsonl"
        transcript.write_text('{"role":"user","text":"hello"}\n')
        workspace.set_root(str(first))

        second = tmp_path / "two"
        workspace.set_root(str(second))

        carried = second / workspace.INTERNAL_DIR / "conversations" / "20260730-1.jsonl"
        assert carried.read_text() == '{"role":"user","text":"hello"}\n'

    def test_the_old_folder_keeps_its_copy(self, isolated, tmp_path):
        first = tmp_path / "one"
        (first / workspace.INTERNAL_DIR).mkdir(parents=True)
        (first / workspace.INTERNAL_DIR / "notes.jsonl").write_text("x")
        workspace.set_root(str(first))

        workspace.set_root(str(tmp_path / "two"))

        # Copied, not moved: the folder someone had before stays a complete thing, so
        # changing this setting is never the reason something is missing from it.
        assert (first / workspace.INTERNAL_DIR / "notes.jsonl").exists()

    def test_work_files_still_stay_behind(self, isolated, tmp_path):
        first = tmp_path / "one"
        (first / workspace.INTERNAL_DIR).mkdir(parents=True)
        first.mkdir(exist_ok=True)
        (first / "report.md").write_text("his work")
        workspace.set_root(str(first))

        second = tmp_path / "two"
        workspace.set_root(str(second))

        # Only his bookkeeping travels. Copying the work as well could mean gigabytes on a
        # settings click, and the interface promises it does not.
        assert not (second / "report.md").exists()

    def test_a_first_run_with_no_records_is_not_an_error(self, isolated, tmp_path):
        workspace.set_root(str(tmp_path / "fresh"))  # no .kith to carry
        assert workspace.configured_root() == tmp_path / "fresh"

    def test_a_failed_copy_leaves_him_where_he_was(self, isolated, tmp_path, monkeypatch):
        first = tmp_path / "one"
        (first / workspace.INTERNAL_DIR).mkdir(parents=True)
        (first / workspace.INTERNAL_DIR / "a.jsonl").write_text("x")
        workspace.set_root(str(first))

        def boom(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(workspace.paths.shutil, "copytree", boom)
        with pytest.raises(workspace.WorkspaceError) as caught:
            workspace.set_root(str(tmp_path / "two"))

        # Pointed at a folder his history cannot be reached from is worse than not moving.
        assert "Leaving him in" in str(caught.value)
        assert workspace.configured_root() == first
