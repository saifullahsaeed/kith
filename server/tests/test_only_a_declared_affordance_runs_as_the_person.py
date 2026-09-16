"""What `POST /plugins/<id>/command/<name>` will and will not run, and why it has to ask.

That route dispatches with `origin="person"`, which **skips the permission gate**. The
justification, written into three docstrings, is that a person clicking an affordance Kith drew
is the authorisation — so the route is safe exactly as long as it refuses anything the manifest
did not offer as an affordance.

It did not check. `documents.py` says "the host refuses any name the manifest did not declare
`in: "surface"`", and the only code that did that lived in `plugin-surface.tsx`: the renderer
enforcing a rule on the server's behalf, one `curl` away from not being enforced at all. The
route also resolved the plugin with `registry.get`, which answers *installed*, not *enabled* —
so switching a plugin off left its commands running.

These are the tests for the server doing its own half.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kith.api import auth

MANIFEST = {
    "manifest": 1,
    "id": "notes",
    "name": "Notes",
    "version": "1.0.0",
    "surfaces": [{"id": "board", "title": "Notes", "entry": "ui/board.html"}],
    "commands": [
        {
            "name": "pin_it",
            "title": "Pin it",
            "delivery": "state",
            "does": {"set": ["pinned"]},
            "params": {"pinned": {"type": "string"}},
            # The opt-in. This is the plugin's author saying its own tab may set this off.
            "present": {"in": "surface"},
        },
        {
            "name": "for_him_only",
            "title": "For him only",
            "delivery": "state",
            "does": {"set": ["noted"]},
            "params": {"noted": {"type": "string"}},
            # The default, and the overwhelming majority: a command that exists for the model to
            # call and has no button anywhere.
            "model": True,
        },
    ],
}


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KITH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KITH_PLUGINS_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("KITH_SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setenv("KITH_WORKSPACE", str(tmp_path / "work"))
    import kith.settings as live

    monkeypatch.setattr(live, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(live, "CONFIG_DB_PATH", tmp_path / "data" / "config.db")
    monkeypatch.setattr(live, "AGENT_DB_PATH", tmp_path / "data" / "agent.db")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    # The grants this route's whole justification rests on live in the config database, and
    # installing a plugin *with commands* is the first thing in these tests that writes one.
    from kith.infra.db import config_store
    from kith.infra.db.migrations import init

    config_store.init(tmp_path / "data" / "config.db")
    # And the agent database, which is where a `state` command's writes land.
    init(tmp_path / "data" / "agent.db")

    from kith.api import routes  # noqa: F401 - the module-level CONFIG_DB_PATH is read at import
    from kith.app import create_app

    monkeypatch.setattr("kith.api.routes.plugins.CONFIG_DB_PATH", tmp_path / "data" / "config.db")
    return create_app().test_client()


@pytest.fixture
def headers(tmp_path: Path):
    return {"X-Kith-Token": auth.token(tmp_path / "data")}


@pytest.fixture
def installed(client, headers, tmp_path: Path):
    source = tmp_path / "source" / "notes"
    (source / "ui").mkdir(parents=True, exist_ok=True)
    (source / "kith.plugin.json").write_text(json.dumps(MANIFEST))
    (source / "ui" / "board.html").write_text("<html><body>notes</body></html>")
    answer = client.post("/api/plugins", json={"path": str(source)}, headers=headers)
    assert answer.status_code == 200, answer.get_json()
    return answer


def run(client, headers, command: str, **payload):
    return client.post(
        f"/api/plugins/notes/command/{command}",
        json={"args": {"pinned": "yes", "noted": "yes"}, "conversation": "c-1", **payload},
        headers=headers,
    )


def test_a_tab_may_set_off_the_command_its_manifest_offered_it(client, headers, installed):
    assert run(client, headers, "pin_it").status_code == 200


def test_a_tab_may_not_set_off_a_command_the_manifest_never_offered_it(client, headers, installed):
    """The bug. `for_him_only` is `in: "none"` — it exists for the model and has no button.

    Before this the route ran it, ungated, for anything that could post to the port. The
    allowlist that was supposed to stop it lived only in the renderer.
    """
    answer = run(client, headers, "for_him_only")

    assert answer.status_code == 403
    assert "does not let its own tab" in answer.get_json()["error"]


def test_chrome_may_not_borrow_a_surfaces_opt_in(client, headers, installed):
    """The two origins are checked against different opt-ins, and neither satisfies the other.

    `in: "surface"` is a statement about the plugin's own tab. A toolbar button is a different
    affordance in a different place, and reading one as consent for the other is how an
    allowlist stops meaning anything.
    """
    answer = run(client, headers, "pin_it", **{"from": "chrome"})

    assert answer.status_code == 403
    assert "draws no button" in answer.get_json()["error"]


def test_an_origin_nobody_recognises_is_refused_rather_than_defaulted(client, headers, installed):
    answer = run(client, headers, "pin_it", **{"from": "somewhere"})

    assert answer.status_code == 400


def test_a_plugin_that_is_switched_off_runs_nothing(client, headers, installed):
    """**Switched off has to mean switched off.**

    `registry.get` answers "is it installed", and this route asked that instead of "is it on".
    A disabled plugin whose buttons still work is the failure `mcp.manager._retire` records one
    layer down: something switched off that went on running.
    """
    assert client.patch("/api/plugins/notes", json={"enabled": False}, headers=headers).status_code == 200

    answer = run(client, headers, "pin_it")

    assert answer.status_code == 404
    assert "switched off" in answer.get_json()["error"]


def test_it_comes_back_when_it_is_switched_on_again(client, headers, installed):
    client.patch("/api/plugins/notes", json={"enabled": False}, headers=headers)
    client.patch("/api/plugins/notes", json={"enabled": True}, headers=headers)

    assert run(client, headers, "pin_it").status_code == 200


def test_an_unknown_icon_is_reachable_by_the_name_the_surface_wrote(client, headers, installed):
    """The fallback only applies if it can be found under the name it is standing in for.

    A document renders one `<symbol id="icon-{name}">` per icon and a surface references
    `#icon-{name}`. Emitting the puzzle glyph under `icon-puzzle` for a surface asking for
    `icon-widget` answered a question nobody had asked, and the frame drew an empty box.
    """
    from kith.services.plugins import icons

    got = icons.named(["widget", "check"])

    assert set(got) == {"widget", "check"}
    assert got["widget"] == icons.ICONS[icons.FALLBACK]
    assert got["check"] == icons.ICONS["check"]
