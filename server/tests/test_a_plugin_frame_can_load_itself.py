"""A plugin surface can fetch its own document, and nothing else about it is open.

This is a regression test for a bug found by driving the real HTTP surface rather than the
services under it: everything worked and the frame came back **401**. An iframe's `src` is a
navigation, and a navigation cannot carry `X-Kith-Token` any more than an `EventSource` can — so
the mount succeeded, the ticket was valid, and the pane rendered nothing with no error anywhere
that named the cause.

The exemption rests entirely on the ticket being unguessable: 24 bytes from
`secrets.token_urlsafe`, handed out over an authenticated POST, buying a document already on this
machine's disk. So the tests below pin **both** halves — that the GET is exempt, and that
everything else about a ticket is not. A stable path here would turn "a secret in the URL" into
"no gate at all", which is why `/api/plugins/<id>/surface/<view>` is deliberately not the URL a
frame loads.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kith.api import auth


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    """The real app, with every path pointed somewhere disposable."""
    monkeypatch.setenv("KITH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KITH_PLUGINS_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("KITH_SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setenv("KITH_WORKSPACE", str(tmp_path / "work"))
    import kith.settings as live

    monkeypatch.setattr(live, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(live, "CONFIG_DB_PATH", tmp_path / "data" / "config.db")
    monkeypatch.setattr(live, "AGENT_DB_PATH", tmp_path / "data" / "agent.db")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)

    from kith.app import create_app

    app = create_app()
    return app.test_client()


@pytest.fixture
def headers(tmp_path: Path):
    return {"X-Kith-Token": auth.token(tmp_path / "data")}


def a_plugin(tmp_path: Path) -> str:
    source = tmp_path / "source" / "tiny"
    (source / "ui").mkdir(parents=True, exist_ok=True)
    (source / "kith.plugin.json").write_text(
        json.dumps(
            {
                "manifest": 1,
                "id": "tiny",
                "name": "Tiny",
                "version": "1.0.0",
                "surfaces": [{"id": "panel", "title": "Tiny", "entry": "ui/panel.html"}],
            }
        )
    )
    (source / "ui" / "panel.html").write_text("<html><body><p>hello</p></body></html>")
    return str(source)


@pytest.fixture
def mounted(client, headers, tmp_path: Path):
    client.post("/api/plugins", json={"path": a_plugin(tmp_path)}, headers=headers)
    answer = client.post(
        "/api/plugins/tiny/surface/panel/mount",
        json={"conversation": "c-1", "client": "w1"},
        headers=headers,
    )
    assert answer.status_code == 200, answer.get_json()
    return answer.get_json()


def test_a_frame_can_load_its_own_document_with_no_header(client, mounted):
    """The whole point. A frame's `src` is a navigation and carries nothing."""
    answer = client.get(mounted["url"])

    assert answer.status_code == 200
    assert b"hello" in answer.data


def test_the_document_arrives_under_the_seal(client, mounted):
    from kith.domain.seal import POLICY

    answer = client.get(mounted["url"])

    # The header is the copy with authority; the `<meta>` in the document is defence in depth
    # for a document mounted some other way. They agree — see `test_the_seal_is_stated_once`.
    assert answer.headers["Content-Security-Policy"] == POLICY
    assert answer.headers["X-Content-Type-Options"] == "nosniff"
    # Never cached: a stale document would outlive the ticket it was served for.
    assert answer.headers["Cache-Control"] == "no-store"


def test_writing_through_a_ticket_still_needs_the_token(client, mounted):
    """Only the GET is exempt. A write is not a navigation, so it has no excuse."""
    answer = client.post(f"/api/plugins/frame/{mounted['ticket']}/state", json={"values": {"a": 1}})
    assert answer.status_code == 401


def test_releasing_a_ticket_still_needs_the_token(client, mounted):
    assert client.delete(f"/api/plugins/frame/{mounted['ticket']}").status_code == 401


def test_an_invented_ticket_gets_nothing(client):
    answer = client.get("/api/plugins/frame/definitely-not-a-real-ticket")
    assert answer.status_code == 404


def test_a_released_ticket_stops_working(client, headers, mounted):
    client.delete(f"/api/plugins/frame/{mounted['ticket']}", headers=headers)
    assert client.get(mounted["url"]).status_code == 404


def test_the_ticket_is_long_enough_to_be_the_gate(mounted):
    """The exemption rests on this and nothing else, so it is worth asserting rather than
    trusting the call that produced it."""
    assert len(mounted["ticket"]) >= 32


def test_only_the_frame_prefix_is_exempt():
    """A stable path here would turn a secret in the URL into no gate at all — so the shape of
    this tuple is the security property, and it is short on purpose."""
    assert auth.OPEN_GET_PREFIXES == ("/api/canvas/", "/api/plugins/frame/")
    assert not any(prefix.rstrip("/").endswith("surface") for prefix in auth.OPEN_GET_PREFIXES)


def test_listing_plugins_is_not_exempt(client):
    assert client.get("/api/plugins").status_code == 401
