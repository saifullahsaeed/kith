"""One folder installs, contributes, switches off and is removed — as one thing.

Kith had three extension surfaces and no way to install one thing: a skill folder, a row in
`mcp.servers`, and an ```html fence that lived for one message. This is the test for the
identity that binds them.

Most of what is asserted here is about **which store owns what**. The manifest says what a
plugin *offers*; the row says what the person *decided*; a grant says whether its program *may
run*. A decision always beats a default, and an upgrade is never a chance to overwrite one —
those two sentences are what stop a plugin re-enabling itself, and they are what the tests below
actually check.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kith.domain.plugins import PluginError, parse
from kith.infra import permissions
from kith.services import skills as skills_service
from kith.services.plugins import install as installer
from kith.services.plugins import registry

MANIFEST = {
    "manifest": 1,
    "id": "circulars",
    "name": "Circular Watch",
    "version": "0.1.0",
    "description": "Watches the regulator and files what it finds.",
}


@pytest.fixture(autouse=True)
def somewhere_to_install(tmp_path: Path, config_db: Path, monkeypatch):
    monkeypatch.setenv("KITH_PLUGINS_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("KITH_SKILLS_DIR", str(tmp_path / "skills"))
    # The skills service asks which plugins are enabled, and it asks the *installed* config
    # database. Without this the composed roots would read the real one.
    monkeypatch.setattr("kith.settings.CONFIG_DB_PATH", config_db)
    registry.forget_cache()
    permissions.revoke_all()
    yield
    registry.forget_cache()
    permissions.revoke_all()


def a_plugin(tmp_path: Path, **overrides) -> Path:
    """A plugin folder on disk, ready to install from."""
    source = tmp_path / "source" / str(overrides.get("id", MANIFEST["id"]))
    source.mkdir(parents=True, exist_ok=True)
    (source / "kith.plugin.json").write_text(json.dumps({**MANIFEST, **overrides}))
    return source


def with_a_skill(source: Path, name: str = "reading-circulars") -> Path:
    folder = source / "skills" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: How to read a regulator's circular.\n---\n\nRead it.\n"
    )
    return source


# --------------------------------------------------------------------------- #
# Trying is not saving
# --------------------------------------------------------------------------- #


def test_reviewing_a_plugin_installs_nothing(tmp_path: Path, config_db: Path):
    report = installer.inspect(a_plugin(tmp_path), config_db)

    assert report["installable"] is True
    assert report["plugin"]["name"] == "Circular Watch"
    assert registry.installed(config_db) == []
    assert not (registry.root() / "circulars").exists()


def test_a_review_prices_what_it_would_cost_every_request(tmp_path: Path, config_db: Path):
    source = a_plugin(
        tmp_path,
        commands=[
            {
                "name": "mark_reviewed",
                "title": "Mark reviewed",
                "description": "Mark one circular as reviewed.",
                "delivery": "state",
                "does": {"set": ["reviewed"]},
                "model": True,
            }
        ],
    )
    report = installer.inspect(source, config_db)

    # A command the model is offered is prompt tokens on every round of every turn, and the
    # install screen is the only place anyone finds that out before agreeing to it.
    assert report["promptChars"] > 0
    assert report["promptTokens"] > 0


# --------------------------------------------------------------------------- #
# The row is the existence test
# --------------------------------------------------------------------------- #


def test_a_folder_dropped_in_by_hand_contributes_nothing(tmp_path: Path, config_db: Path):
    stray = registry.root() / "circulars"
    stray.mkdir(parents=True)
    (stray / "kith.plugin.json").write_text(json.dumps(MANIFEST))

    assert registry.installed(config_db) == []
    # Reported, though — "I dropped it in and nothing happened" needs an answer on screen.
    assert [p["kind"] for p in registry.health(config_db)] == ["stray"]


def test_installing_copies_it_in_and_records_the_decision(tmp_path: Path, config_db: Path):
    installer.install(config_db, a_plugin(tmp_path))

    assert [p.id for p in registry.installed(config_db)] == ["circulars"]
    assert registry.row(config_db, "circulars")["enabled"] is True
    # Off by default. It is the only thing in the design that spends tokens on every turn
    # forever, so a manifest may offer one and only a person may switch it on.
    assert registry.row(config_db, "circulars")["digest"] is False


def test_nothing_is_left_behind_when_a_plugin_is_refused(tmp_path: Path, config_db: Path):
    broken = a_plugin(tmp_path, id="circulars", surfaces=[{"id": "board", "title": "B"}])

    with pytest.raises(PluginError):
        installer.install(config_db, broken)

    assert registry.installed(config_db) == []
    staging = registry.root() / registry.STAGING
    assert not staging.exists() or list(staging.iterdir()) == []


# --------------------------------------------------------------------------- #
# A decision beats a default
# --------------------------------------------------------------------------- #


def test_an_upgrade_does_not_re_enable_a_plugin_you_switched_off(tmp_path: Path, config_db: Path):
    installer.install(config_db, a_plugin(tmp_path))
    installer.set_enabled(config_db, "circulars", False)

    installer.install(config_db, a_plugin(tmp_path, version="0.2.0"))

    assert registry.row(config_db, "circulars")["enabled"] is False
    assert registry.row(config_db, "circulars")["version"] == "0.2.0"


def test_an_upgrade_keeps_the_credentials_you_typed(tmp_path: Path, config_db: Path):
    source = a_plugin(tmp_path, server={"command": "npx", "args": ["notes@1.0.0"], "env": ["TOKEN"]})
    installer.install(config_db, source, env={"TOKEN": "secret"})

    installer.install(
        config_db,
        a_plugin(
            tmp_path,
            id="circulars",
            version="0.2.0",
            server={"command": "npx", "args": ["notes@1.0.0"], "env": ["TOKEN"]},
        ),
    )

    assert registry.row(config_db, "circulars")["env"] == {"TOKEN": "secret"}


def test_a_disabled_plugin_contributes_nothing(tmp_path: Path, config_db: Path):
    installer.install(config_db, with_a_skill(a_plugin(tmp_path)))
    assert "reading-circulars" in [s.name for s in skills_service.installed()]

    installer.set_enabled(config_db, "circulars", False)

    assert "reading-circulars" not in [s.name for s in skills_service.installed()]


# --------------------------------------------------------------------------- #
# Skills, composed
# --------------------------------------------------------------------------- #


def test_a_plugins_skill_is_discovered_not_declared(tmp_path: Path, config_db: Path):
    installer.install(config_db, with_a_skill(a_plugin(tmp_path)))

    found = [s for s in skills_service.installed() if s.name == "reading-circulars"]
    assert found and found[0].owner == "circulars"


def test_the_index_does_not_pay_for_attribution(tmp_path: Path, config_db: Path):
    """Provenance is level 2, not level 1. In the index it would cost tokens on every request
    forever to answer a question that does not change whether he opens the skill."""
    installer.install(config_db, with_a_skill(a_plugin(tmp_path)))
    index = skills_service.index()

    # The line is the bare name and description, exactly as a skill in the person's own folder
    # reads. No owner, no "(from …)", nothing that costs tokens to say who wrote it.
    assert "**reading-circulars** — How to read a regulator's circular." in index
    assert "Circular Watch" not in index
    assert "plugin" not in index.lower()

    # Level 2 carries it instead, which is the moment he is about to follow the instructions.
    assert skills_service.read("reading-circulars")["from"] == "circulars"


def test_your_own_skill_wins_a_collision(tmp_path: Path, config_db: Path):
    mine = skills_service.root() / "reading-circulars"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text(
        "---\nname: reading-circulars\ndescription: Mine, which I wrote.\n---\n\nMine.\n"
    )

    # Refused at the door, rather than installing and silently contributing nothing.
    with pytest.raises(PluginError, match="cannot shadow a skill you wrote"):
        installer.install(config_db, with_a_skill(a_plugin(tmp_path)))


def test_a_plugins_skill_cannot_be_removed_from_the_skills_screen(tmp_path: Path, config_db: Path):
    installer.install(config_db, with_a_skill(a_plugin(tmp_path)))

    with pytest.raises(skills_service.SkillError, match="Remove the plugin"):
        skills_service.remove("reading-circulars")


# --------------------------------------------------------------------------- #
# The server, and the grant that lets it run
# --------------------------------------------------------------------------- #


def test_a_plugins_server_never_lands_in_your_own_configuration(tmp_path: Path, config_db: Path):
    from kith.domain.mcp import MCPServer
    from kith.services.mcp import manager

    installer.install(config_db, a_plugin(tmp_path, server={"command": "npx", "args": ["x@1.0.0"]}))

    assert "circulars" in [s.label for s in manager.configured(config_db)]
    # A row that round-trips into the blob outlives its plugin: the settings page PUTs the
    # whole list, so it would be written back and nothing would ever remove it again.
    manager.save(config_db, [MCPServer(label="mine", command="npx")])
    assert [s.label for s in manager._stored_rows(config_db)] == ["mine"]


def test_you_cannot_take_a_plugins_label(tmp_path: Path, config_db: Path):
    from kith.domain.mcp import MCPServer
    from kith.services.mcp import manager

    installer.install(config_db, a_plugin(tmp_path, server={"command": "npx", "args": ["x@1.0.0"]}))

    with pytest.raises(ValueError, match="is the 'circulars' plugin's server"):
        manager.save(config_db, [MCPServer(label="circulars", command="npx")])


def test_an_ungranted_server_is_not_offered_at_all(tmp_path: Path, config_db: Path):
    installer.install(config_db, a_plugin(tmp_path, server={"command": "npx", "args": ["x@1.0.0"]}))
    plugin = registry.get(config_db, "circulars")
    assert plugin is not None
    permissions.revoke(registry.spawn_signature(plugin))

    # Not started, not listed, not in any prompt — decided before any turn exists, so nothing
    # can shrink the tools block mid-turn.
    assert registry.mcp_servers(config_db) == []


def test_an_unpinned_runner_is_refused(tmp_path: Path, config_db: Path):
    """`npx -y pkg` fetches whatever is newest when it runs, so the grant would be against code
    nobody reviewed and no digest would notice the change."""
    source = a_plugin(tmp_path, server={"command": "npx", "args": ["-y", "notes-mcp"]})

    with pytest.raises(PluginError, match="Pin a version"):
        installer.install(config_db, source)


def test_a_manifest_carrying_a_credential_is_refused(tmp_path: Path):
    source = a_plugin(tmp_path, server={"command": "npx", "env": {"TOKEN": "hunter2"}})

    with pytest.raises(PluginError, match="already leaked"):
        parse(source)


# --------------------------------------------------------------------------- #
# Refusing what cannot be understood
# --------------------------------------------------------------------------- #


def test_an_unknown_field_inside_a_block_is_a_fault(tmp_path: Path):
    """A misspelled field is a plugin that silently never starts, which is the failure this
    whole subsystem is worst at surfacing."""
    source = a_plugin(tmp_path, server={"comand": "npx"})

    with pytest.raises(PluginError, match="does not know"):
        parse(source)


def test_an_unknown_top_level_field_is_kept(tmp_path: Path):
    """The opposite rule, and the reason is the one `services/skills.py` gives: refusing to
    parse what we do not implement is how "any plugin works" stops being true a month later."""
    plugin = parse(a_plugin(tmp_path, futureThing={"a": 1}))

    assert plugin.problems() == []
    assert plugin.public()["unsupportedFields"] == ["futureThing"]


def test_a_newer_manifest_keeps_its_row_and_says_what_it_needs(tmp_path: Path, config_db: Path):
    installer.install(config_db, a_plugin(tmp_path))
    # Kith updates; this plugin is written for a version it does not understand.
    (registry.root() / "circulars" / "kith.plugin.json").write_text(json.dumps({**MANIFEST, "manifest": 99}))

    trouble = registry.health(config_db)

    assert [p["kind"] for p in trouble] == ["incompatible"]
    # Never auto-uninstalled: silently removing tabs, skills and a subprocess from a running
    # system on an update is the single most likely real-world breakage here.
    assert registry.row(config_db, "circulars") is not None


# --------------------------------------------------------------------------- #
# Removal
# --------------------------------------------------------------------------- #


def test_removing_a_plugin_revokes_what_let_it_run(tmp_path: Path, config_db: Path):
    installer.install(config_db, a_plugin(tmp_path, server={"command": "npx", "args": ["x@1.0.0"]}))
    plugin = registry.get(config_db, "circulars")
    assert plugin is not None
    signature = registry.spawn_signature(plugin)
    assert permissions.granted(signature)

    installer.uninstall(config_db, "circulars")

    # A grant that outlives the thing granted is the whole attack against reinstall.
    assert not permissions.granted(signature)
    assert not permissions.granted("plugin:circulars:*")


def test_removing_a_plugin_keeps_its_data_for_a_while(tmp_path: Path, config_db: Path):
    installer.install(config_db, a_plugin(tmp_path))

    installer.uninstall(config_db, "circulars")

    row = registry.rows(config_db)["circulars"]
    assert row["retiredAt"]
    assert registry.installed(config_db) == []


def test_asking_to_delete_the_data_deletes_the_row(tmp_path: Path, config_db: Path):
    installer.install(config_db, a_plugin(tmp_path))

    installer.uninstall(config_db, "circulars", delete_state=True)

    assert "circulars" not in registry.rows(config_db)
