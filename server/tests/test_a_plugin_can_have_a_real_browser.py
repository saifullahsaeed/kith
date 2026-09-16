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
import time
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


def test_resizing_the_viewport_is_a_thing_a_person_does():
    """`resize` lays the page out at a size — the thing a person does when they check a layout
    at a phone's width. The bound that keeps it on the closed list: it names a **viewport**,
    never a window. Sizing Kith's own window would reach past the pane and touch everything
    around it; a viewport inside the pane touches nothing but the page."""
    assert "resize" in VIEW_ACTS


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

    def fake(plugin: str, view: str, act: str, owner: str = "", args: dict | None = None) -> dict:
        asked.append((plugin, view, act, args or {}))
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


def test_a_resize_call_reaches_the_shell_with_its_size(monkeypatch, installed, db: Path, config_db: Path):
    """The size travels as arguments, coerced by the manifest's declaration like any other
    command's — nothing about the act is special enough to skip the gate or the shaping."""
    from dataclasses import replace

    asked: list[tuple] = []

    def fake(plugin: str, view: str, act: str, owner: str = "", args: dict | None = None) -> dict:
        asked.append((plugin, view, act, args or {}))
        return {"width": 390, "height": 844, "url": "https://example.com/", "title": "Example"}

    monkeypatch.setattr(renderer, "browse", fake)
    resize = replace(
        installed.commands[0],
        name="resize",
        does={"act": "resize"},
        params={
            "preset": {"type": "string", "enum": ["reset", "phone", "desktop"]},
            "width": {"type": "integer", "minimum": 200, "maximum": 3840},
        },
        required=(),
    )
    answer = commands.dispatch(db, config_db, installed, resize, {"preset": "phone"}, origin="person")

    assert asked == [("browse", "page", "resize", {"preset": "phone"})]
    assert answer["width"] == 390


def test_the_browsers_own_manifest_resizes_cleanly():
    """The repo's own plugin is the one manifest every reader can check the schema against, so
    it must parse with no problems at all — and it must actually offer the resize this file
    tests for, or the examples have drifted out from under the feature."""
    source = Path(__file__).resolve().parents[2] / "examples" / "plugins" / "browser"
    if not source.is_dir():
        pytest.skip("the example plugins are not in this checkout")

    plugin = parse(source)

    assert plugin.problems() == []
    resize = next((command for command in plugin.commands if command.name == "resize"), None)
    assert resize is not None
    assert resize.does.get("act") == "resize"
    assert resize.model


# --------------------------------------------------------------------------- #
# Asking Kith to put the tab on screen
# --------------------------------------------------------------------------- #


def a_host_command(folder: Path) -> Plugin:
    """A manifest whose one command asks Kith to open its own surface."""
    return declared(
        folder,
        commands=[
            {
                "name": "show",
                "title": "Open the Browser tab",
                "delivery": "host",
                "surface": "page",
                "does": {"host": "open_surface"},
                "params": {},
                "returns": {"opened": {"type": "boolean"}},
                "model": True,
            }
        ],
    )


def test_a_plugin_may_ask_kith_to_open_its_own_tab(folder: Path):
    """The dead end this closes: the browser's `look` refuses when its tab is shut and told him
    to ask the person to open it — and he had no way to open it himself, so the commonest thing
    he needs from a surface was the one thing he could not do."""
    assert a_host_command(folder).problems() == []


def test_an_effect_kith_does_not_perform_yet_is_refused_at_install(folder: Path):
    """Four of the five host effects are vocabulary with nothing behind them. Declaring one used
    to install cleanly, appear on the review screen, and refuse every call at runtime — a
    documented field that does nothing, found from somebody else's bug report."""
    problems = declared(
        folder,
        commands=[
            {
                "name": "fold",
                "title": "Fold",
                "delivery": "host",
                "surface": "page",
                "does": {"host": "fold_conversation"},
                "model": True,
            }
        ],
    ).problems()

    assert any("does not do yet" in why for why in problems)


def test_a_host_effect_waits_for_the_app_and_returns_what_it_did(
    monkeypatch, installed, db: Path, config_db: Path
):
    """It parks on the same queue a `surface` call uses, so it inherits the deadline, the
    per-window claim and the wake-on-stop rather than growing a second set of clocks."""
    import threading

    from kith.kernel import live_turns, session_context
    from kith.services.plugins import calls

    plugin = a_host_command(Path(installed.path))
    command = plugin.commands[0]

    def app_answers() -> None:
        for _ in range(80):
            time.sleep(0.05)
            # The app's collector asks for host calls only — a frame asking for `surface` must
            # not be handed this one, which is what `kind` is for.
            waiting = calls.pending("c-host", client="a-window", kind="host")
            if waiting:
                calls.reply(waiting[0]["id"], {"opened": True})
                return

    turn = live_turns.begin("c-host")
    try:
        with session_context.working_in("c-host"):
            threading.Thread(target=app_answers, daemon=True).start()
            answer = commands.dispatch(db, config_db, plugin, command, {}, origin="person")
    finally:
        live_turns.finish(turn)
        calls.release("c-host")
        calls.forget_misses()

    assert answer.get("opened") is True


def test_a_frame_is_not_handed_an_effect_it_cannot_perform(monkeypatch, installed, db: Path, config_db: Path):
    """**The subtle one.** `pending` *claims* what it hands out, so two windows never race to
    answer the same call. Both collectors poll the same route — so without the kind, a mounted
    frame would claim a host effect it has no way to perform, and the call would sit until its
    deadline with the app never seeing it."""
    import threading

    from kith.kernel import live_turns, session_context
    from kith.services.plugins import calls

    plugin = a_host_command(Path(installed.path))
    seen: list[list[dict]] = []

    def a_frame_looks() -> None:
        time.sleep(0.1)
        # What `plugin-surface.tsx` asks for.
        seen.append(calls.pending("c-host", client="a-frame", kind="surface"))
        waiting = calls.pending("c-host", client="a-window", kind="host")
        if waiting:
            calls.reply(waiting[0]["id"], {"opened": True})

    turn = live_turns.begin("c-host")
    try:
        with session_context.working_in("c-host"):
            threading.Thread(target=a_frame_looks, daemon=True).start()
            answer = commands.dispatch(db, config_db, plugin, plugin.commands[0], {}, origin="person")
    finally:
        live_turns.finish(turn)
        calls.release("c-host")
        calls.forget_misses()

    assert seen[0] == []
    assert answer.get("opened") is True


# --------------------------------------------------------------------------- #
# One browser per conversation
# --------------------------------------------------------------------------- #


def test_each_conversation_gets_its_own_browser(monkeypatch, installed, db: Path, config_db: Path):
    """**What makes parallel work possible.** A view was keyed by plugin and surface alone, so
    there was one browser for the whole app: a page opened while working on one thing was the
    same page as the one opened while working on another, and two errands side by side fought
    over it — one navigating away under the other mid-read."""
    from kith.kernel import session_context

    asked: list[str] = []
    monkeypatch.setattr(
        renderer, "browse", lambda p, v, a, owner="", **k: asked.append(owner) or {"url": "u"}
    )

    with session_context.working_in("chat-one"):
        commands.dispatch(db, config_db, installed, installed.commands[0], {"url": "x"}, origin="person")
    with session_context.working_in("chat-two"):
        commands.dispatch(db, config_db, installed, installed.commands[0], {"url": "y"}, origin="person")

    assert asked == ["chat-one", "chat-two"]


def test_a_surface_that_answers_for_the_app_shares_one_browser(
    monkeypatch, tmp_path: Path, folder: Path, config_db: Path, db: Path
):
    """The other half of the choice, and the reason it is a manifest field rather than a rule.
    A plugin may want one browser for the app; `answers: "any"` says so, and a person reviewing
    the plugin can see which it asked for."""
    from kith.kernel import session_context

    monkeypatch.setenv("KITH_PLUGINS_DIR", str(tmp_path / "plugins"))
    monkeypatch.setattr("kith.settings.CONFIG_DB_PATH", config_db)
    registry.forget_cache()
    plugin = surfaced(folder, answers="any")

    asked: list[str] = []
    monkeypatch.setattr(
        renderer, "browse", lambda p, v, a, owner="", **k: asked.append(owner) or {"url": "u"}
    )

    with session_context.working_in("chat-one"):
        commands.dispatch(db, config_db, plugin, plugin.commands[0], {"url": "x"}, origin="person")
    with session_context.working_in("chat-two"):
        commands.dispatch(db, config_db, plugin, plugin.commands[0], {"url": "y"}, origin="person")

    assert asked == ["", ""]
    registry.forget_cache()


def test_a_browser_cannot_be_opened_twice_in_one_conversation(folder: Path):
    """Two panes of one browser would be two rectangles for one native view, each telling the
    shell to put it somewhere else — so they would fight at whatever rate the panes re-measure.
    A second *conversation* getting its own view is the thing anybody actually wants, and that
    is `answers`."""
    problems = surfaced(folder, instances="many").problems()

    assert any("many instances" in why for why in problems)
