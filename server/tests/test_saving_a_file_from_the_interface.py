"""The one write that does not come from him.

Every other path into ``write_file`` is the agent acting on his own, which is what the
permission gate exists for — he writes unprompted, so a person answering a prompt is the
protection. This route is the opposite situation: the person *is* the one who pressed save.

Worth its own file because the two failure modes are invisible rather than loud. Reusing
``require_path`` would not have raised anything — it parks the calling thread until somebody
answers, and the calling thread here is an HTTP request, so a save outside the workspace would
hang until it timed out and look to the editor like a save that quietly did nothing. And the
containment has to hold without the prompt doing any of the work, because the prompt is the
part that was removed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith.api import auth


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    """The real app, with every path pointed somewhere disposable."""
    monkeypatch.setenv("KITH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KITH_PLUGINS_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("KITH_SKILLS_DIR", str(tmp_path / "skills"))
    import kith.settings as live

    monkeypatch.setattr(live, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(live, "CONFIG_DB_PATH", tmp_path / "data" / "config.db")
    monkeypatch.setattr(live, "AGENT_DB_PATH", tmp_path / "data" / "agent.db")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)

    # `create_app` initialises the config database at the path `app.py` bound when it was
    # imported, which is not the one monkeypatched above — so the store the permission check
    # reads through has to be opened at the patched path explicitly. Without this the gate
    # fails on "no such table: settings" and the test reports a routing bug that is not there.
    from kith.infra.db import config_store

    config_store.init(tmp_path / "data" / "config.db")

    from kith.app import create_app

    return create_app().test_client()


@pytest.fixture
def headers(tmp_path: Path):
    return {"X-Kith-Token": auth.token(tmp_path / "data")}


@pytest.fixture(autouse=True)
def asking_rather_than_bypassing(monkeypatch):
    """Ask mode, so the gate is actually doing something in these tests.

    In bypass, ``check_path`` returns allowed on its first line and every assertion below
    would pass for the wrong reason — the test would be asserting the mode, not the route.
    """
    from kith.infra import permissions

    monkeypatch.setattr(permissions, "mode", lambda: permissions.Mode.ASK)


class TestSaving:
    def test_a_file_in_his_workspace_is_written(self, client, headers, never_the_real_workspace):
        answer = client.put(
            "/api/workspace/file",
            json={"path": "screens/home.html", "content": "<div data-k=a>hi</div>"},
            headers=headers,
        )
        assert answer.status_code == 200
        assert (never_the_real_workspace / "screens" / "home.html").read_text() == "<div data-k=a>hi</div>"

    def test_saving_twice_replaces_rather_than_appends(self, client, headers, never_the_real_workspace):
        for content in ("<p>one</p>", "<p>two</p>"):
            client.put(
                "/api/workspace/file",
                json={"path": "a.html", "content": content},
                headers=headers,
            )
        assert (never_the_real_workspace / "a.html").read_text() == "<p>two</p>"

    def test_the_same_path_reads_back_what_was_written(self, client, headers):
        client.put(
            "/api/workspace/file",
            json={"path": "a.html", "content": "<p>hi</p>"},
            headers=headers,
        )
        read = client.get("/api/workspace/file?path=a.html", headers=headers)
        assert read.get_json()["content"] == "<p>hi</p>"


class TestRefusals:
    def test_a_write_outside_his_folders_is_answered_not_awaited(self, client, headers, tmp_path):
        """The one that would have hung.

        403 is the assertion; *returning at all* is the point. If this route ever goes back to
        ``require_path``, this test stops failing loudly and starts timing out instead.
        """
        outside = tmp_path / "elsewhere" / "notes.html"
        answer = client.put(
            "/api/workspace/file",
            json={"path": str(outside), "content": "<p>hi</p>"},
            headers=headers,
        )
        assert answer.status_code == 403
        assert not outside.exists()

    def test_a_path_that_climbs_out_lands_nowhere(self, client, headers, tmp_path):
        answer = client.put(
            "/api/workspace/file",
            json={"path": "../../escaped.html", "content": "<p>hi</p>"},
            headers=headers,
        )
        assert answer.status_code == 403
        assert not (tmp_path / "escaped.html").exists()
        assert not (tmp_path.parent / "escaped.html").exists()

    def test_content_that_is_not_text_is_refused(self, client, headers):
        answer = client.put(
            "/api/workspace/file",
            json={"path": "a.html", "content": {"not": "text"}},
            headers=headers,
        )
        assert answer.status_code == 400

    def test_a_missing_path_is_refused(self, client, headers):
        assert (
            client.put("/api/workspace/file", json={"content": "<p>hi</p>"}, headers=headers).status_code
            == 400
        )

    def test_saving_still_needs_the_token(self, client):
        answer = client.put("/api/workspace/file", json={"path": "a.html", "content": "<p>hi</p>"})
        assert answer.status_code == 401
