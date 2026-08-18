"""The two endpoints behind the panel's banner.

Every part of the shared board was reachable only by the model until these existed, which meant
a person working through the interface could not see that somebody else's work was sitting in
the folder, let alone take it. A feature only an agent can operate is half a feature.

The GET describes and the POST applies, which is the same split `preview`/`pull` already make —
worth having twice because the panel must be able to show a person what will happen before they
press anything.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith.infra import project_files
from kith.infra.db import repositories as repo


@pytest.fixture
def client():
    """A test client on the real blueprint.

    `create_app` rather than a hand-built Flask app: the routes are registered by importing
    `kith.api.routes`, and an app that skipped that would be testing a URL map this project
    does not have. The autouse fixtures in `conftest` already point the data directory and the
    databases somewhere temporary, and stop the scheduler thread.
    """
    from kith import settings
    from kith.api import auth
    from kith.app import create_app

    made = create_app()
    made.config["TESTING"] = True
    given = made.test_client()
    # The panel is a browser and the API is authenticated. A client without the header tests
    # only that the gate works, which `test_api_auth` already does.
    given.environ_base[f"HTTP_{auth.HEADER.upper().replace('-', '_')}"] = auth.token(settings.DATA_DIR)
    return given


@pytest.fixture
def shared(client, db: Path, tmp_path: Path, monkeypatch):
    from kith.api.routes import shared_board

    monkeypatch.setattr(shared_board, "AGENT_DB_PATH", db)
    folder = tmp_path / "repo"
    folder.mkdir()
    project = repo.projects.add_project(db, "Shared", "", str(folder))
    return client, db, int(project["id"]), folder


class TestSeeingWhatIsThere:
    def test_a_quiet_folder_says_so_without_erroring(self, shared):
        client, _, project_id, _ = shared
        answered = client.get(f"/api/projects/{project_id}/shared")
        assert answered.status_code == 200, answered.get_data(as_text=True)[:300]
        body = answered.get_json()
        assert body["added"] == [] and body["blocked"] == ""

    def test_it_names_what_somebody_else_filed(self, shared):
        client, _, project_id, folder = shared
        project_files.write_brief(
            folder, {"id": 900, "key": "1a015eaff8309001abc", "goal": "Theirs", "status": "working"}
        )
        body = client.get(f"/api/projects/{project_id}/shared").get_json()
        assert body["added"] == ["Theirs"]
        assert body["folder"] == str(folder)

    def test_looking_changes_nothing(self, shared):
        client, db, project_id, folder = shared
        project_files.write_brief(
            folder, {"id": 900, "key": "1a015eaff8309001abc", "goal": "Theirs", "status": "working"}
        )
        client.get(f"/api/projects/{project_id}/shared")
        assert repo.tasks.list_tasks(db) == []

    def test_a_project_with_no_folder_is_not_an_error(self, client, db: Path, monkeypatch):
        """A shortlist or a piece of research is a project with rows and no folder. The panel
        renders the same page for it and must not show a failure."""
        from kith.api.routes import shared_board

        monkeypatch.setattr(shared_board, "AGENT_DB_PATH", db)
        made = repo.projects.add_project(db, "No folder", "")
        body = client.get(f"/api/projects/{made['id']}/shared").get_json()
        assert body["folder"] == "" and body["added"] == []

    def test_a_project_that_does_not_exist_is_a_404(self, client, db: Path, monkeypatch):
        from kith.api.routes import shared_board

        monkeypatch.setattr(shared_board, "AGENT_DB_PATH", db)
        assert client.get("/api/projects/99999/shared").status_code == 404


class TestTakingItIn:
    def test_it_applies_what_the_look_described(self, shared):
        client, db, project_id, folder = shared
        project_files.write_brief(
            folder, {"id": 900, "key": "1a015eaff8309001abc", "goal": "Theirs", "status": "working"}
        )
        looked = client.get(f"/api/projects/{project_id}/shared").get_json()
        done = client.post(f"/api/projects/{project_id}/shared").get_json()
        assert looked["added"] == done["added"] == ["Theirs"]
        assert [t["goal"] for t in repo.tasks.list_tasks(db)] == ["Theirs"]

    def test_an_unreadable_folder_is_refused_here_too(self, shared):
        """The banner shows a warning instead of a button, and the button being pressed anyway
        — an old page, a second tab — must not import half a merge."""
        client, db, project_id, folder = shared
        (folder / ".kith" / "tasks").mkdir(parents=True, exist_ok=True)
        (folder / ".kith" / "tasks" / "99-t.md").write_text("# T\n<<<<<<< HEAD\nx\n")
        body = client.post(f"/api/projects/{project_id}/shared").get_json()
        assert body["blocked"]
        assert repo.tasks.list_tasks(db) == []

    def test_a_project_with_no_folder_refuses_rather_than_pretending(self, client, db: Path, monkeypatch):
        from kith.api.routes import shared_board

        monkeypatch.setattr(shared_board, "AGENT_DB_PATH", db)
        made = repo.projects.add_project(db, "No folder", "")
        answered = client.post(f"/api/projects/{made['id']}/shared")
        assert answered.status_code == 400
        assert "no folder" in answered.get_json()["error"]
