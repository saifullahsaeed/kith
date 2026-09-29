"""Finding the design system, in the one place that can see everywhere.

Three things need the same answer about what `var(--ds-accent)` is — the renderer, the
inspector's swatches, and the checker that says whether a screen obeys the system. The one thing
worse than no system is three of them disagreeing.

The interface cannot resolve this on its own, and that is the whole reason this route exists. The
owner's brand has lived in `brand/profile.json` *inside a skill* since long before this surface,
and the workspace routes cannot see into the skills directory. The interface's answer was to copy
it into the workspace — which is how two files come to disagree by next month. Resolving here
means the file stays where it was authored and nothing is ever a copy.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kith.api import auth

SADEEF = {
    "name": "Sadeef",
    "tokens": {"colors": {"accent": "59C5C7", "ink": "343C42"}},
    "radii_px": {"card": 20},
}

SCREENS = {"name": "Screens", "colors": {"accent": "#111111"}}


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KITH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KITH_PLUGINS_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("KITH_SKILLS_DIR", str(tmp_path / "skills"))
    import kith.settings as live

    monkeypatch.setattr(live, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(live, "CONFIG_DB_PATH", tmp_path / "data" / "config.db")
    monkeypatch.setattr(live, "AGENT_DB_PATH", tmp_path / "data" / "agent.db")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)

    # `create_app` initialises both databases at the paths `app.py` bound when it was imported,
    # which are not the ones monkeypatched above — so they are opened here explicitly. Without
    # this the project tests fail on "no such table: projects" and read as a routing bug.
    from kith.infra.db import config_store, migrations

    config_store.init(tmp_path / "data" / "config.db")
    migrations.init(tmp_path / "data" / "agent.db")

    from kith.app import create_app

    return create_app().test_client()


@pytest.fixture
def headers(tmp_path: Path):
    return {"X-Kith-Token": auth.token(tmp_path / "data")}


def _write(folder: Path, name: str, doc: dict) -> Path:
    target = folder / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(doc), encoding="utf-8")
    return target


class TestFinding:
    def test_no_system_anywhere_is_an_answer_not_an_error(self, client, headers):
        answer = client.get("/api/design-system", headers=headers)
        assert answer.status_code == 200
        body = answer.get_json()
        assert body["path"] == "" and body["content"] == ""
        # The inventory and the conventions are empty rather than absent, so nothing downstream
        # has to tell "no system" apart from "a system with no parts".
        assert body["components"] == {} and body["conventions"] == ""

    def test_a_system_in_his_workspace(self, client, headers, never_the_real_workspace):
        _write(never_the_real_workspace, "design-system.json", SCREENS)
        body = client.get("/api/design-system", headers=headers).get_json()
        assert json.loads(body["content"])["name"] == "Screens"

    def test_a_brand_profile_inside_a_skill(self, client, headers, tmp_path):
        """The case the interface could not reach, and the reason this route exists."""
        _write(tmp_path / "skills" / "sadeef-design-engine", "brand/profile.json", SADEEF)
        body = client.get("/api/design-system", headers=headers).get_json()
        assert json.loads(body["content"])["name"] == "Sadeef"

    def test_the_workspace_outranks_a_skill(self, client, headers, tmp_path, never_the_real_workspace):
        _write(tmp_path / "skills" / "sadeef-design-engine", "brand/profile.json", SADEEF)
        _write(never_the_real_workspace, "design-system.json", SCREENS)
        body = client.get("/api/design-system", headers=headers).get_json()
        assert json.loads(body["content"])["name"] == "Screens"

    def test_one_written_for_screens_outranks_one_adapted_into_them(
        self, client, headers, never_the_real_workspace
    ):
        # Within a folder: a system authored for this surface beats a profile authored for slides.
        _write(never_the_real_workspace, "brand/profile.json", SADEEF)
        _write(never_the_real_workspace, "design-system.json", SCREENS)
        body = client.get("/api/design-system", headers=headers).get_json()
        assert json.loads(body["content"])["name"] == "Screens"

    def test_json_that_is_not_a_system_is_passed_over(self, client, headers, never_the_real_workspace):
        # A file can sit at the path and be something else entirely; the next candidate wins.
        _write(never_the_real_workspace, "design-system.json", {"unrelated": True})
        _write(never_the_real_workspace, "brand/profile.json", SADEEF)
        body = client.get("/api/design-system", headers=headers).get_json()
        assert json.loads(body["content"])["name"] == "Sadeef"


class TestPerProject:
    def test_a_project_folder_outranks_the_workspace(
        self, client, headers, tmp_path, never_the_real_workspace
    ):
        """Several products, several brands. The one you are working in wins."""
        from kith.infra.db import repositories as repo
        from kith.settings import AGENT_DB_PATH

        folder = tmp_path / "vigil"
        _write(folder, "design-system.json", {"name": "Vigil", "colors": {"accent": "#ff0000"}})
        _write(never_the_real_workspace, "design-system.json", SCREENS)

        project = repo.projects.add_project(AGENT_DB_PATH, "Vigil", directory=str(folder))
        pid = project["id"] if isinstance(project, dict) else project

        body = client.get(f"/api/design-system?projectId={pid}", headers=headers).get_json()
        assert json.loads(body["content"])["name"] == "Vigil"

    def test_a_project_with_no_system_of_its_own_falls_back(self, client, headers, never_the_real_workspace):
        from kith.infra.db import repositories as repo
        from kith.settings import AGENT_DB_PATH

        _write(never_the_real_workspace, "design-system.json", SCREENS)
        project = repo.projects.add_project(AGENT_DB_PATH, "No brand")
        pid = project["id"] if isinstance(project, dict) else project

        body = client.get(f"/api/design-system?projectId={pid}", headers=headers).get_json()
        assert json.loads(body["content"])["name"] == "Screens"


def test_it_still_needs_the_token(client):
    assert client.get("/api/design-system").status_code == 401
