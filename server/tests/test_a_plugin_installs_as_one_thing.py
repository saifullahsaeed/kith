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
from kith.infra import confinement, permissions
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


A_COMMAND = {
    "name": "file_it",
    "title": "File it",
    "delivery": "state",
    "does": {"set": ["last"]},
    "model": True,
}


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


def test_an_upgrade_keeps_what_the_plugin_wrote(tmp_path: Path, config_db: Path):
    """**Install replaces the plugin's folder, so nothing the plugin owns may live inside it.**

    It did. `home_for` was `<plugins>/<id>/.home`, and install is an `rmtree` and one `os.rename`
    over that same folder — so every upgrade destroyed every file the plugin had written, while
    the store went on holding the *paths* to them. The browser plugin came back from an upgrade
    with a store that said it had a screenshot and a tab that could not show one.

    Two paths in `confinement` and one constant in `registry` have to agree about where storage
    is for this to hold, and none of them can see the other two, so this is the test that they
    do.
    """
    source = a_plugin(tmp_path)
    installer.install(config_db, source)
    kept = confinement.home_for("circulars")
    kept.mkdir(parents=True, exist_ok=True)
    (kept / "a-screenshot.png").write_bytes(b"\x89PNG")

    installer.install(config_db, a_plugin(tmp_path, version="0.2.0"))

    assert (confinement.home_for("circulars") / "a-screenshot.png").read_bytes() == b"\x89PNG"


def test_a_plugin_cannot_read_another_plugins_files(tmp_path: Path, config_db: Path):
    """Storage sits under one shared folder, so the boundary has to name the plugin's own and
    never its parent. The profile used to work the id back out of the storage path, which
    answered ".storage" once storage moved — granting a read over all of them at once."""
    installer.install(config_db, a_plugin(tmp_path))
    resolved = confinement.resolve(None, "circulars")

    written = confinement.profile(resolved, confinement.home_for("circulars"), plugin_id="circulars")

    # The quote closes, so this matches a rule naming the shared root *itself* and not the
    # plugin's own folder inside it.
    assert f'"{registry.root() / registry.STORAGE}"' not in written
    assert f'"{confinement.home_for("circulars")}"' in written


def test_deleting_a_plugins_data_deletes_its_files_too(tmp_path: Path, config_db: Path):
    """The other half of moving storage out. It used to go with the code folder — which is how
    it came to be destroyed on an upgrade — so the delete path never had to name it."""
    installer.install(config_db, a_plugin(tmp_path))
    home = confinement.home_for("circulars")
    home.mkdir(parents=True, exist_ok=True)
    (home / "held.png").write_bytes(b"x")

    installer.uninstall(config_db, "circulars", delete_state=True)

    assert not home.exists()


def test_uninstalling_keeps_a_plugins_files_the_way_it_keeps_its_state(tmp_path: Path, config_db: Path):
    """Uninstall marks rather than deletes, and its own comment says storage is the person's
    data on the same thirty-day rule. It trashed it with the code."""
    installer.install(config_db, a_plugin(tmp_path))
    home = confinement.home_for("circulars")
    home.mkdir(parents=True, exist_ok=True)
    (home / "held.png").write_bytes(b"x")

    installer.uninstall(config_db, "circulars")

    assert (home / "held.png").exists()


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


def test_a_settings_save_survives_a_plugin_that_ships_a_server(tmp_path: Path, config_db: Path):
    """**The two guards that were both load-bearing, and the one that was being defeated.**

    The settings page PUTs the whole list, and `GET /api/mcp` shows it a plugin's server, so it
    echoed one back. `save` guards against that with `if not s.owner` — a filter that only works
    if the field survives the round trip. The client's `MCPServer` type dropped `owner`, so an
    echoed row arrived looking like the person's own, slipped past the filter, and hit the label
    check instead: a 400 that failed the *whole* save, including the row they were actually
    editing. Latent until a plugin shipped a server, and unmissable once one did.

    Fixed in the client, which no longer sends them. This is the second answer, here: a row
    carrying its owner is dropped rather than refused, so an older window that still echoes one
    saves the person's changes instead of losing them.
    """
    from dataclasses import replace as _replace

    from kith.domain.mcp import MCPServer
    from kith.services.mcp import manager

    installer.install(config_db, a_plugin(tmp_path, server={"command": "npx", "args": ["x@1.0.0"]}))
    theirs = next(s for s in manager.configured(config_db) if s.label == "circulars")

    # What the page holds after a GET: the person's row and the plugin's, saved together.
    manager.save(config_db, [MCPServer(label="mine", command="npx"), theirs])

    assert [s.label for s in manager._stored_rows(config_db)] == ["mine"]
    # And the plugin's is still offered, from its own store rather than the person's.
    assert "circulars" in [s.label for s in manager.configured(config_db)]
    # The label check still refuses a person genuinely trying to take the name — that refusal is
    # correct, and it is what the echo was being mistaken for.
    with pytest.raises(ValueError, match="is the 'circulars' plugin's server"):
        manager.save(config_db, [_replace(theirs, owner="")])


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


def test_an_upgrade_does_not_leave_the_old_permission_behind(tmp_path: Path, config_db: Path):
    """**Three grants for a plugin with no program, which is how this was found.**

    A spawn signature covers the resolved reach, the seal and the command line, so any change to
    what gets spawned produces a different signature — deliberately, so a widened boundary
    re-asks rather than inheriting consent. Nothing collected the previous one, so a plugin
    upgraded three times held three grants and the Permissions pane listed all of them.
    """
    installer.install(config_db, a_plugin(tmp_path, server={"command": "npx", "args": ["x@1.0.0"]}))
    first = permissions.plugin_spawn_grants("circulars")
    assert len(first) == 1

    # A different command line is a different boundary, so a different signature.
    installer.install(config_db, a_plugin(tmp_path, server={"command": "npx", "args": ["x@2.0.0"]}))

    now = permissions.plugin_spawn_grants("circulars")
    assert len(now) == 1
    assert now != first


def test_a_plugin_that_drops_its_program_drops_the_permission(tmp_path: Path, config_db: Path):
    """The sharper half. The browser plugin stopped shipping a subprocess and kept two standing
    grants saying one could run — for a plugin that no longer had one at all."""
    installer.install(
        config_db,
        a_plugin(tmp_path, server={"command": "npx", "args": ["x@1.0.0"]}, commands=[A_COMMAND]),
    )
    assert permissions.plugin_spawn_grants("circulars")

    # The same plugin, now with no program of its own.
    installer.install(config_db, a_plugin(tmp_path, commands=[A_COMMAND]))

    assert permissions.plugin_spawn_grants("circulars") == []
    # Its commands are still granted — that is a separate decision and a separate namespace.
    assert permissions.granted("plugin:circulars:*")


def test_a_command_grant_never_lets_a_program_run(tmp_path: Path, config_db: Path):
    """The invariant the settings screen was misreporting.

    `plugin:<id>:*` grants the plugin's declared *commands*; `plugin:<id>:<seal>:<hash>` grants
    its program the right to run. The grants list read the seal out of the wrong segment and
    described a command grant as "its program may run, with your full access" — for plugins that
    ship no program. False, and alarming in the one place a person goes to check.
    """
    installer.install(
        config_db,
        a_plugin(tmp_path, server={"command": "npx", "args": ["x@1.0.0"]}, commands=[A_COMMAND]),
    )
    spawn = permissions.plugin_spawn_grants("circulars")[0]
    permissions.revoke(spawn)

    # The command grant survives and covers commands...
    assert permissions.granted("plugin:circulars:*")
    # ...and does not answer for the program. Segment-wise, so a sibling id cannot either.
    assert not permissions.granted(spawn)


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


def test_deleting_a_plugins_data_forgets_what_its_browser_remembered(
    tmp_path: Path, config_db: Path, monkeypatch
):
    """**The privacy hole this closes.** A `web` surface has a session partition in the desktop
    app's own storage — cookies, local storage, live logins — and nothing cleared it. So
    "remove this plugin and delete its data" left signed-in accounts on disk indefinitely, and
    reinstalling the plugin silently inherited them.

    It has to be a request to the shell: the session lives in the other process and this one
    cannot reach it.
    """
    from kith.infra import renderer

    wiped: list[str] = []
    monkeypatch.setattr(renderer, "forget_plugin_browser", lambda one: wiped.append(one) or True)
    installer.install(config_db, a_plugin(tmp_path))

    installer.uninstall(config_db, "circulars", delete_state=True)

    assert wiped == ["circulars"]


def test_removing_a_plugin_closes_its_browser_but_keeps_the_logins(
    tmp_path: Path, config_db: Path, monkeypatch
):
    """Two acts, and the difference is the person's data.

    Removing a plugin stops its program, so a renderer process still holding its pages is waste.
    What the browser *remembers* is the same bytes as its stored state, which uninstall marks
    rather than deletes — so it follows the same thirty-day rule instead of going here.
    """
    from kith.infra import renderer

    closed: list[str] = []
    wiped: list[str] = []
    monkeypatch.setattr(renderer, "close_plugin_browser", lambda one: closed.append(one))
    monkeypatch.setattr(renderer, "forget_plugin_browser", lambda one: wiped.append(one) or True)
    installer.install(config_db, a_plugin(tmp_path))

    installer.uninstall(config_db, "circulars")

    assert closed == ["circulars"]
    assert wiped == []


def test_switching_a_plugin_off_stops_its_browser_too(tmp_path: Path, config_db: Path, monkeypatch):
    """`_reconnect` stops the plugin's program; nothing stopped its browser. A disabled plugin
    holding a live renderer process — and a tab still painting over the app — was the one part
    of it that went on running after being switched off."""
    from kith.infra import renderer

    closed: list[str] = []
    monkeypatch.setattr(renderer, "close_plugin_browser", lambda one: closed.append(one))
    installer.install(config_db, a_plugin(tmp_path))

    installer.set_enabled(config_db, "circulars", False)

    assert closed == ["circulars"]
