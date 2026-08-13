"""A project linked to an external folder makes that folder the working base — ticks and chat both.

The brain used to default to ~/Kith and scaffold its own folder even when a project pointed at
~/Desktop/my-app. The fix is base_dir(), derived from the active session's project. Permissions are
untouched: a linked folder was already a free zone (permissions._inside_linked_project), so this
only decides WHERE relative paths and a command's cwd land, never WHAT he may touch.
"""

from kith.infra import workspace
from kith.infra.db import repositories as repo
from kith.services import conversations, session_context


def _link(db, tmp_path, monkeypatch):
    """A project linked to a real external folder, with a session working on it."""
    monkeypatch.setattr("kith.settings.AGENT_DB_PATH", db)
    monkeypatch.setattr(workspace.paths.settings, "WORKSPACE_DIR", str(tmp_path / "kith-home"), raising=False)
    proj_dir = tmp_path / "external-project"
    proj_dir.mkdir()
    proj = repo.projects.add_project(db, "App", "", str(proj_dir))
    conv = conversations.start(db, "work")["id"]
    repo.conversations.set_project(db, conv, proj["id"])
    return proj_dir, conv


def test_base_dir_is_the_linked_folder_inside_a_session(db, tmp_path, monkeypatch):
    proj_dir, conv = _link(db, tmp_path, monkeypatch)
    with session_context.working_in(conv):
        assert workspace.base_dir() == proj_dir


def test_base_dir_falls_back_to_root_with_no_session(db, tmp_path, monkeypatch):
    _link(db, tmp_path, monkeypatch)  # a linked project exists, but nothing claims it right now
    assert workspace.base_dir() == workspace.root()


def test_relative_paths_resolve_into_the_linked_folder(db, tmp_path, monkeypatch):
    proj_dir, conv = _link(db, tmp_path, monkeypatch)
    with session_context.working_in(conv):
        assert workspace.resolve("lib/db.ts") == str(proj_dir / "lib" / "db.ts")
        assert workspace.resolve("") == str(proj_dir)
    # outside the session, a relative path anchors to the workspace root, exactly as before
    assert workspace.resolve("lib/db.ts") == str(workspace.root() / "lib" / "db.ts")


def test_absolute_paths_are_still_left_alone(db, tmp_path, monkeypatch):
    _proj_dir, conv = _link(db, tmp_path, monkeypatch)
    with session_context.working_in(conv):
        assert workspace.resolve("/etc/hosts") == "/etc/hosts"


def test_a_project_with_no_folder_keeps_the_workspace_root(db, tmp_path, monkeypatch):
    monkeypatch.setattr("kith.settings.AGENT_DB_PATH", db)
    monkeypatch.setattr(workspace.paths.settings, "WORKSPACE_DIR", str(tmp_path / "kith-home"), raising=False)
    proj = repo.projects.add_project(db, "Research", "", None)  # a project with rows, no folder
    conv = conversations.start(db, "work")["id"]
    repo.conversations.set_project(db, conv, proj["id"])
    with session_context.working_in(conv):
        assert workspace.base_dir() == workspace.root()
