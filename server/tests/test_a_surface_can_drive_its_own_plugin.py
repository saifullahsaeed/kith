"""A person clicking inside a plugin's own tab, and the two bounds that make it safe.

Kith's plugin surfaces were a one-way mirror. A tab could show you what the plugin had done and
hold a coordinate you clicked, but everything a plugin's program can actually *do* reaches the
model as an `mcp__<plugin>__<tool>` call — and only the model can make one. So the browser
plugin could show you a page and had no way to let you type an address into it: your own click
had nowhere to go.

Two things close that, and both are bounds rather than capabilities.

`delivery: "server"` routes a declared command onto one of the plugin's own tools. Nothing new
is granted — the program is already running under a grant somebody gave at install, and this
calls a tool it already offers.

`present: {in: "surface"}` says which of those the plugin's own tab may set off. That list is
the whole authorisation: the renderer forwards a name on it and drops everything else. What is
asserted here is that `host` delivery can never be on it, whatever a manifest says, because
those five effects — folding a conversation, opening a tab, moving the app around — must stay
behind chrome Kith drew.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from kith.domain.plugins import Plugin, parse
from kith.kernel import session_context
from kith.services.plugins import commands, registry, state
from kith.services.plugins import install as installer

MANIFEST = {
    "manifest": 1,
    "id": "browse",
    "name": "Browser",
    "version": "0.1.0",
    "description": "A page he drives and you can reach into.",
    "server": {"command": "npx", "args": ["browse-mcp@1.0.0"]},
    "surfaces": [{"id": "board", "title": "Browser", "entry": "ui/board.html"}],
}

A_TOOL_COMMAND = {
    "name": "navigate",
    "title": "Go to an address",
    "delivery": "server",
    "does": {"tool": "open"},
    "params": {"url": {"type": "string"}},
    "required": ["url"],
    "present": {"in": "surface"},
    "model": False,
}


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    """Somewhere to write a manifest. `parse` reads a folder, because a plugin is a folder —
    a manifest with no directory around it is not a thing Kith can be handed."""
    where = tmp_path / "browse"
    where.mkdir()
    return where


def declared(folder: Path, **over) -> Plugin:
    """One manifest with one command, written and read back, overridden per case."""
    body = {**MANIFEST, "commands": [{**A_TOOL_COMMAND, **over}]}
    (folder / "kith.plugin.json").write_text(json.dumps(body))
    return parse(folder)


# --------------------------------------------------------------------------- #
# What a manifest may declare
# --------------------------------------------------------------------------- #


def test_a_command_the_plugins_own_program_performs(folder: Path):
    assert declared(folder).problems() == []


def test_a_server_command_that_names_no_tool_is_refused(folder: Path):
    """Otherwise it parses, installs, and refuses every call at runtime — a fault the author
    finds from somebody else's bug report rather than from the review screen."""
    problems = declared(folder, does={}).problems()

    assert any("names no tool" in why for why in problems)


def test_a_tab_may_set_off_its_own_plugins_command(folder: Path):
    assert declared(folder).commands[0].from_surface() is True


def test_a_tab_may_not_set_off_one_of_kiths_own_effects(folder: Path):
    """**The line.** A surface may drive its own plugin; it may not drive Kith.

    A page inside the seal has no verb that reaches outside its own plugin, and that is what
    lets a click on it count as authorisation at all. Folding a conversation is not that.
    """
    problems = declared(folder, delivery="host", does={"host": "fold"}, present={"in": "surface"}).problems()

    assert any("may not drive Kith" in why for why in problems)


def test_a_host_command_is_not_forwardable_even_if_it_slipped_through(folder: Path):
    """Belt and braces, at the layer the renderer actually asks. `problems()` refuses this
    manifest, so this is the second answer for a row already in the database — an older Kith's
    install, or a hand-edited one."""
    assert declared(folder, delivery="host", does={"host": "fold"}).commands[0].from_surface() is False


def test_a_typo_in_where_it_is_presented_is_a_fault_not_a_silence(folder: Path):
    """`in: "surfaces"` would parse, install, declare nothing, and drop every click. The author
    has no way to tell that from the renderer refusing them."""
    problems = declared(folder, present={"in": "surfaces"}).problems()

    assert any("presented in 'surfaces'" in why for why in problems)


def test_a_command_is_not_surface_invocable_unless_it_says_so(folder: Path):
    """The default. Almost every command exists for him to call, and chrome for it is the
    exception — so this is the answer that must not need declaring."""
    assert declared(folder, present={}).commands[0].from_surface() is False


# --------------------------------------------------------------------------- #
# What the renderer is told
# --------------------------------------------------------------------------- #


def test_the_index_and_the_mounted_frame_agree_on_what_a_tab_may_do(folder: Path):
    """Two callers, one rule.

    `registry.surfaces` fills the block the renderer boots with; the mount route answers for the
    frame it is about to serve. Each had its own copy of the rule, which is the shape the seal
    constants were in when one of them drifted and stopped matching the policy it stated.
    """
    plugin = declared(folder)

    # The domain is the single answer both now call.
    assert [one.name for one in plugin.commands if one.from_surface()] == ["navigate"]


# --------------------------------------------------------------------------- #
# Performing one
# --------------------------------------------------------------------------- #


class FakeManager:
    """The plugin's own program, as far as this test is concerned.

    A real one is a subprocess over stdio, which `test_a_plugins_program_runs_inside_a_boundary`
    covers. What matters here is what happens to the *answer*.
    """

    def __init__(self, answer: dict):
        self.answer = answer
        self.asked: list[tuple[str, dict]] = []

    def run(self, name: str, args: dict, timeout: float) -> dict:
        self.asked.append((name, args))
        return dict(self.answer)


@pytest.fixture
def a_running_plugin(monkeypatch, tmp_path: Path, folder: Path, config_db: Path) -> Iterator[Plugin]:
    """An installed, enabled plugin whose program is up.

    Genuinely installed rather than parsed: what the program hands back goes into the plugin's
    store, and the store resolves a plugin's *scope* from the installed row. A parsed manifest
    has no row, so the write is refused — which is what this fixture existing at all is for.
    """
    monkeypatch.setenv("KITH_PLUGINS_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("KITH_SKILLS_DIR", str(tmp_path / "skills"))
    # The store asks `settings.CONFIG_DB_PATH`, not the database it was handed.
    monkeypatch.setattr("kith.settings.CONFIG_DB_PATH", config_db)
    registry.forget_cache()
    declared(folder)
    # The entry the manifest names has to be there — install checks, because a surface whose
    # document is missing is a tab that opens on nothing.
    (folder / "ui").mkdir()
    (folder / "ui" / "board.html").write_text("<div id=root></div>")
    installer.install(config_db, folder)
    plugin = registry.get(config_db, "browse")
    assert plugin is not None
    # Up, without starting anything. Whether the subprocess is running is a different test's
    # subject — `test_a_plugins_program_runs_inside_a_boundary` — and starting one here would
    # make every case in this file wait on npx.
    monkeypatch.setattr(registry, "mcp_servers", lambda _db: [type("Row", (), {"label": "browse"})()])
    yield plugin
    registry.forget_cache()


def test_it_calls_the_plugins_own_tool(monkeypatch, a_running_plugin, db: Path, config_db: Path):
    manager = FakeManager({"url": "https://example.com"})
    monkeypatch.setattr("kith.services.mcp.manager.run", manager.run)

    answer = commands.dispatch(
        db,
        config_db,
        a_running_plugin,
        a_running_plugin.commands[0],
        {"url": "https://example.com"},
        origin="person",
    )

    # Namespaced to the plugin, so a command can only ever reach that plugin's own tools.
    assert manager.asked == [("mcp__browse__open", {"url": "https://example.com"})]
    assert answer["url"] == "https://example.com"


def test_what_the_program_changed_lands_in_the_store(
    monkeypatch, a_running_plugin, db: Path, config_db: Path
):
    """The reserved key, lifted here as well as on the model's path.

    Without it a click would work and the tab would not move, which reads as the click having
    missed — the plugin failing at the one thing this delivery exists to make possible.
    """
    manager = FakeManager({"ok": True, "_kith_state": {"url": "https://example.com", "title": "Ex"}})
    monkeypatch.setattr("kith.services.mcp.manager.run", manager.run)

    with session_context.working_in("c-1"):
        answer = commands.dispatch(
            db,
            config_db,
            a_running_plugin,
            a_running_plugin.commands[0],
            {"url": "https://example.com"},
            origin="person",
        )
        held = state.read(db, "browse")["values"]
        assert held["url"] == "https://example.com"
        assert held["title"] == "Ex"
    # And it is not handed back as if it were a return value.
    assert "_kith_state" not in answer


def test_a_command_for_a_program_that_is_not_running_says_so(
    a_running_plugin, db: Path, config_db: Path, monkeypatch
):
    monkeypatch.setattr(registry, "mcp_servers", lambda _db: [])

    answer = commands.dispatch(
        db,
        config_db,
        a_running_plugin,
        a_running_plugin.commands[0],
        {"url": "https://example.com"},
        origin="person",
    )

    assert answer["ok"] is False
    assert answer["code"] == "plugin_gone"
