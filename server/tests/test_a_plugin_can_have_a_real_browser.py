"""A tab that is a browser rather than a picture of one.

The browser plugin used to run headless Chromium in its own subprocess and post PNGs into a
sealed frame. That works, and it is a photograph: you cannot scroll it, type into it, or log in
with it. The person's complaint was exactly that — "i cant, it just a image".

The seal is why, and the seal is not the thing to change. A plugin surface is
`sandbox="allow-scripts"` over `default-src 'none'` precisely so a stranger's code can run in
this window; loosening it for the one plugin that wants a network would loosen it for every
plugin that asks in the same words.

So a browser is a different **kind** of surface. `kind: "web"` runs no plugin code at all: the
Electron shell composites real web contents over the pane, the person drives it with their
hands, and the model drives the same page through `view` commands. What is asserted here is the
part that has to hold for that to be safe and to be usable — that a `web` surface has no
document, that a plugin cannot script the page, and that a call with no app running says so in
a sentence rather than failing silently.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from kith.domain.plugins import VIEW_ACTS, Plugin, parse
from kith.infra import renderer
from kith.services.plugins import commands, registry
from kith.services.plugins import install as installer

MANIFEST = {
    "manifest": 1,
    "id": "browse",
    "name": "Browser",
    "version": "0.1.0",
    "description": "A browser in a tab.",
    "surfaces": [{"id": "page", "title": "Browser", "kind": "web"}],
    "commands": [
        {
            "name": "open",
            "title": "Open a page",
            "delivery": "view",
            "surface": "page",
            "does": {"act": "open"},
            "params": {"url": {"type": "string"}},
            "required": ["url"],
            "model": True,
        }
    ],
}


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    where = tmp_path / "browse"
    where.mkdir()
    return where


def declared(folder: Path, **over) -> Plugin:
    body = {**MANIFEST, **over}
    (folder / "kith.plugin.json").write_text(json.dumps(body))
    return parse(folder)


def surfaced(folder: Path, **over) -> Plugin:
    """One manifest whose single surface is overridden per case."""
    return declared(folder, surfaces=[{**MANIFEST["surfaces"][0], **over}])


def commanded(folder: Path, **over) -> Plugin:
    return declared(folder, commands=[{**MANIFEST["commands"][0], **over}])


# --------------------------------------------------------------------------- #
# What a browser surface is
# --------------------------------------------------------------------------- #


def test_a_surface_can_declare_that_it_is_a_browser(folder: Path):
    plugin = declared(folder)

    assert plugin.problems() == []
    assert plugin.surfaces[0].kind == "web"


def test_a_document_is_still_the_default(folder: Path):
    """Every surface written before this existed is a sealed document, and must stay one. A
    default that changed under them would turn every plugin's own page into a browser."""
    plugin = surfaced(folder, kind=None, entry="ui/board.html")

    assert plugin.surfaces[0].kind == "document"


def test_a_browser_has_no_page_of_its_own(folder: Path):
    """An entry would be a document, and the shell provides the browser. Refused rather than
    ignored: a manifest naming a page nothing will ever load is an author who believes something
    untrue about their own tab."""
    problems = surfaced(folder, entry="ui/board.html").problems()

    assert any("has no entry page" in why for why in problems)


def test_a_browser_is_not_a_place_to_push_files(folder: Path):
    problems = surfaced(folder, assets=["shot"]).problems()

    assert any("nothing pushes files into it" in why for why in problems)


def test_a_browser_may_start_somewhere(folder: Path):
    assert surfaced(folder, home="https://example.com").problems() == []


def test_where_it_starts_has_to_be_an_address(folder: Path):
    problems = surfaced(folder, home="file:///etc/passwd").problems()

    assert any("not an http(s) address" in why for why in problems)


# --------------------------------------------------------------------------- #
# What a plugin may ask a browser to do
# --------------------------------------------------------------------------- #


def test_the_things_a_person_does_with_their_hands(folder: Path):
    """The closed list, and the reason it is closed. These are the verbs of using a browser."""
    assert "open" in VIEW_ACTS
    assert "read" in VIEW_ACTS
    assert "click" in VIEW_ACTS


def test_a_plugin_cannot_run_script_in_the_page():
    """**The line for this delivery.**

    A plugin that could evaluate JavaScript in the pane would not need any of the other acts,
    and the pane would stop being Kith's browser and become the plugin's — with the person's
    session in it, and no list of what it had done.
    """
    assert not {"eval", "evaluate", "script", "inject"} & VIEW_ACTS


def test_an_unknown_act_is_refused_by_name(folder: Path):
    problems = commanded(folder, does={"act": "eval"}).problems()

    assert any("asks a browser pane to 'eval'" in why for why in problems)


def test_a_view_command_must_name_a_browser_the_plugin_has(folder: Path):
    problems = commanded(folder, surface="nowhere").problems()

    assert any("does not have" in why for why in problems)


def test_commands_nobody_can_invoke_are_a_fault(folder: Path):
    """`model` is opt-in and defaults false, which is right — a command costs prompt tokens on
    every round of every turn. The failure mode was silent and total: eight commands, no `model`
    key, installs clean, reports nothing, and he is handed none of them. Which happened while
    this plugin was being written, to somebody who had just read the schema."""
    problems = commanded(folder, model=False).problems()

    assert any("can be invoked by anything" in why for why in problems)
    assert any('"model": true' in why for why in problems)


# --------------------------------------------------------------------------- #
# Performing one
# --------------------------------------------------------------------------- #


@pytest.fixture
def installed(monkeypatch, tmp_path: Path, folder: Path, config_db: Path) -> Iterator[Plugin]:
    monkeypatch.setenv("KITH_PLUGINS_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("KITH_SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setattr("kith.settings.CONFIG_DB_PATH", config_db)
    registry.forget_cache()
    declared(folder)
    installer.install(config_db, folder)
    plugin = registry.get(config_db, "browse")
    assert plugin is not None
    yield plugin
    registry.forget_cache()


def test_it_asks_the_shell_to_drive_the_page(monkeypatch, installed, db: Path, config_db: Path):
    asked: list[tuple] = []

    def fake(plugin: str, view: str, act: str, **args) -> dict:
        asked.append((plugin, view, act, args))
        return {"url": "https://example.com/", "title": "Example"}

    monkeypatch.setattr(renderer, "browse", fake)

    answer = commands.dispatch(
        db, config_db, installed, installed.commands[0], {"url": "https://example.com"}, origin="person"
    )

    assert asked == [("browse", "page", "open", {"url": "https://example.com"})]
    # The address after redirects, which is the fact he needs and the one he cannot infer.
    assert answer["url"] == "https://example.com/"


def test_no_app_running_is_a_sentence_rather_than_a_silence(
    monkeypatch, installed, db: Path, config_db: Path
):
    """The browser is a view in the app's own window, so a scheduled turn at four in the morning
    has nothing to drive. He has to be told that in words he can act on — the same courtesy
    `_ask_surface` extends to a tab that is merely shut."""
    monkeypatch.setattr(renderer, "browse", lambda *a, **k: None)

    answer = commands.dispatch(
        db, config_db, installed, installed.commands[0], {"url": "https://example.com"}, origin="person"
    )

    assert answer["ok"] is False
    # The same code the other deliveries use for the same fact, rather than a browser-shaped
    # one. What differs about a browser is a *shut tab*, and that is `no_view`.
    assert answer["code"] == "no_renderer"
    assert "No window is open" in answer["error"]


def test_a_shut_tab_says_which_tab_to_open(monkeypatch, installed, db: Path, config_db: Path):
    monkeypatch.setattr(renderer, "browse", lambda *a, **k: {"error": "browse's page browser is not open."})

    answer = commands.dispatch(
        db, config_db, installed, installed.commands[0], {"url": "https://example.com"}, origin="person"
    )

    assert answer["ok"] is False
    assert answer["code"] == "no_view"
    # The shell's own sentence, passed through rather than flattened into a code the model then
    # has to interpret.
    assert "is not open" in answer["error"]


def test_a_screenshot_comes_back_as_a_path_not_as_bytes(monkeypatch, installed, db: Path, config_db: Path):
    """The bargain the old plugin struck and this one keeps: a look costs about fifty tokens
    until he decides the picture is worth thousands."""
    import base64
    from dataclasses import replace

    png = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()
    monkeypatch.setattr(renderer, "browse", lambda *a, **k: {"url": "u", "title": "t", "png": png})
    look = replace(installed.commands[0], name="look", does={"act": "look"}, params={}, required=())
    answer = commands.dispatch(db, config_db, installed, look, {}, origin="person")

    assert "png" not in answer
    assert answer["shot"].endswith(".png")
    assert Path(answer["shot"]).read_bytes().startswith(b"\x89PNG")
