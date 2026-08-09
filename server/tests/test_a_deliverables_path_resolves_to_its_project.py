"""A relative path he wrote lands where he wrote it, not wherever the interface happened
to be looking.

`resolve()` anchors a relative path to the *active session's* linked project — right when he
writes it, a real turn, a real project bound. Looking at it again later is a plain GET from
the interface with no session at all, so it fell back to the global workspace root every
time: a task's deliverable, a plan doc attached to `update_task`, any relative path shown for
a project with its own folder. "there's no .kith/work/task-76.md" was the global root telling
the truth about the wrong folder — the file was never missing, the request just never said
which project it belonged to.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith.api.routes import workspace as route
from kith.infra import workspace as ws
from kith.infra.db import repositories as repo


@pytest.fixture(autouse=True)
def workspace_root(tmp_path, monkeypatch):
    """The fallback these tests check *against* is a real folder — the global default
    workspace — so it has to be a throwaway one, not whatever `~/Kith` holds on this machine."""
    monkeypatch.setattr(ws.paths.settings, "WORKSPACE_DIR", str(tmp_path / "kith-home"), raising=False)


def _get(monkeypatch, db, path, project_id=None):
    from flask import Flask

    monkeypatch.setattr(route, "AGENT_DB_PATH", db)
    args = {"path": path}
    if project_id is not None:
        args["projectId"] = str(project_id)
    with Flask(__name__).test_request_context("/api/workspace/file", query_string=args):
        out = route.workspace_file()
    body, status = out if isinstance(out, tuple) else (out, 200)
    return status, body.get_json()


class TestAnchoringToTheRightProject:
    def test_a_relative_path_finds_the_projects_folder(self, db, tmp_path, monkeypatch):
        project = repo.projects.add_project(db, "App", "", str(tmp_path))
        work = tmp_path / ".kith" / "work"
        work.mkdir(parents=True)
        (work / "task-76.md").write_text("the plan")

        status, body = _get(monkeypatch, db, ".kith/work/task-76.md", project["id"])

        assert status == 200
        assert body["content"] == "the plan"

    def test_with_no_project_id_it_is_unchanged(self, db, tmp_path, monkeypatch):
        """The global workspace root stays the answer for everything that never named a
        project — a chat attachment, a script, the ordinary file browser."""
        (tmp_path / "task-76.md").write_text("the plan")

        status, body = _get(monkeypatch, db, "task-76.md")

        assert status == 400
        assert "there's no" in body["error"]

    def test_a_project_with_no_folder_falls_back_unchanged(self, db, monkeypatch):
        project = repo.projects.add_project(db, "No folder", "")

        status, body = _get(monkeypatch, db, "nope.md", project["id"])

        assert status == 400
        assert "there's no" in body["error"]

    def test_an_unknown_project_id_falls_back_unchanged(self, db, monkeypatch):
        status, body = _get(monkeypatch, db, "nope.md", 999_999)

        assert status == 400
        assert "there's no" in body["error"]

    def test_an_absolute_path_is_never_reanchored(self, db, tmp_path, monkeypatch):
        """Absolute means absolute — a project's folder must not be prepended to a path
        that already names its own location."""
        project = repo.projects.add_project(db, "App", "", str(tmp_path / "elsewhere"))
        real = tmp_path / "real.md"
        real.write_text("the real one")

        status, body = _get(monkeypatch, db, str(real), project["id"])

        assert status == 200
        assert body["content"] == "the real one"


class TestTheAnchorHelperItself:
    def test_joins_a_relative_path_to_the_directory(self, db, tmp_path, monkeypatch):
        monkeypatch.setattr(route, "AGENT_DB_PATH", db)
        project = repo.projects.add_project(db, "App", "", str(tmp_path))

        anchored = route._anchor("a/b.md", str(project["id"]))

        assert anchored == str(Path(tmp_path) / "a" / "b.md")

    def test_no_project_id_is_a_no_op(self, db, monkeypatch):
        monkeypatch.setattr(route, "AGENT_DB_PATH", db)
        assert route._anchor("a/b.md", None) == "a/b.md"

    def test_a_bad_project_id_is_a_no_op_not_an_error(self, db, monkeypatch):
        monkeypatch.setattr(route, "AGENT_DB_PATH", db)
        assert route._anchor("a/b.md", "not-a-number") == "a/b.md"
