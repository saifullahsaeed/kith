"""The Browser tab is Kith's own, not a plugin's.

It was a plugin: a manifest of nine commands and a skill, wrapped around a browser the desktop
shell drew. The plugin supplied nothing the shell did not, and its nine schemas cost about 3,900
characters on every request whether or not anybody browsed. So it is one built-in tool, the skill
ships with the other bundled skills — which is what lets an update deliver a change to it — and
a plugin can no longer declare a browser of its own.

What is asserted here is the part the server owns: that a call reaches the right conversation's
browser, that each answer carries what its act says and not the shell's whole status, that a
screenshot comes back as a path, that `show` waits for the window, that a plugin cannot be a
browser any more, and that an install of the old plugin is retired rather than left broken with
its stale skill in the way of the bundled one.
"""

from __future__ import annotations

import base64
import json
import shutil
import threading
import time
from pathlib import Path

import pytest

from kith.domain.plugins import PluginError, parse
from kith.infra import renderer
from kith.kernel import live_turns, session_context
from kith.services import skills as skills_service
from kith.services.plugins import calls, registry
from kith.services.plugins import install as installer
from kith.tools import browser as browser_tool
from kith.tools import run_tool
from kith.tools.registry import require

SERVER = Path(__file__).resolve().parents[1]


@pytest.fixture
def shell(monkeypatch) -> list[tuple]:
    """A desktop shell that records what it was asked and answers with a page."""
    asked: list[tuple] = []

    def browse(act: str, owner: str = "", args: dict | None = None) -> dict:
        asked.append((act, owner, args or {}))
        return {"owner": owner, "url": "https://example.com/", "title": "Example", "failure": ""}

    monkeypatch.setattr(renderer, "browse", browse)
    return asked


def call(db: Path, **args) -> dict:
    return browser_tool.browser(db, args)


# --------------------------------------------------------------------------- #
# Driving it
# --------------------------------------------------------------------------- #


def test_it_asks_the_shell_for_this_conversations_browser(shell, db: Path):
    with session_context.working_in("chat-one"):
        answer = call(db, act="open", url="example.com")

    assert shell == [("open", "chat-one", {"url": "example.com", "new_tab": False, "private": False})]
    assert answer == {"url": "https://example.com/", "title": "Example"}


def test_each_conversation_gets_its_own_browser(shell, db: Path):
    """**What makes parallel work possible.** One browser for the whole app meant two errands
    side by side fought over one page — one navigating away under the other mid-read."""
    with session_context.working_in("chat-one"):
        call(db, act="read")
    with session_context.working_in("chat-two"):
        call(db, act="read")

    assert [owner for _, owner, _ in shell] == ["chat-one", "chat-two"]


def test_no_app_running_is_a_sentence_rather_than_a_silence(monkeypatch, db: Path):
    monkeypatch.setattr(renderer, "browse", lambda *a, **k: None)

    answer = call(db, act="read")

    assert "No window is open" in answer["error"]


def test_the_shells_own_refusal_reaches_him_word_for_word(monkeypatch, db: Path):
    """A browser refuses for reasons only it knows — a field that is not there, a key it cannot
    press — and the refusal lists what *is* there. That list is the useful half of a miss."""
    said = 'Nothing on the page is a field called "Phone", so nothing was typed. The fields on the page are: Email.'
    monkeypatch.setattr(renderer, "browse", lambda *a, **k: {"error": said})

    answer = call(db, act="type", text="555", into="Phone")

    assert answer["error"] == said


@pytest.mark.parametrize(
    ("args", "missing"),
    [
        ({"act": "open"}, "url"),
        ({"act": "click"}, "text"),
        ({"act": "type"}, "text"),
        ({"act": "press"}, "key"),
        ({"act": "press", "key": "Delete"}, "key"),
        ({"act": "switch"}, "tab"),
        ({"act": "wait"}, "wait needs"),
    ],
)
def test_an_act_missing_what_it_needs_is_refused_before_the_shell(shell, db: Path, args, missing):
    answer = call(db, **args)

    assert missing in answer["error"]
    assert shell == []


def test_an_answer_keeps_what_its_act_says_and_drops_the_rest(monkeypatch, db: Path):
    """A tool result is re-read on every later round of the turn. The shell reports a dozen status
    fields about every page — whether it can go back, whether it is loading, the viewport — and
    carrying all of them on every read is paying for them a dozen times over."""
    monkeypatch.setattr(
        renderer,
        "browse",
        lambda *a, **k: {
            "owner": "c",
            "url": "https://example.com/",
            "title": "Example",
            "loading": False,
            "canGoBack": True,
            "canGoForward": False,
            "failure": "",
            "viewport": None,
            "scale": 1,
            "text": "Hello",
        },
    )

    assert call(db, act="read") == {"url": "https://example.com/", "title": "Example", "text": "Hello"}


def test_what_is_clickable_comes_back_as_words(monkeypatch, db: Path):
    monkeypatch.setattr(
        renderer, "browse", lambda *a, **k: {"url": "u", "clicked": "", "options": ["Log in", "", "Sign up"]}
    )

    assert call(db, act="click", text="Issues")["options"] == "Log in, Sign up"


def test_a_screenshot_comes_back_as_a_path_not_as_bytes(monkeypatch, tmp_path: Path, db: Path):
    """A path costs him about fifty tokens and the picture costs thousands, and whether it is worth
    looking at is his call after seeing the answer."""
    picture = base64.b64encode(b"\x89PNG not really").decode()
    monkeypatch.setattr(browser_tool, "_shots", lambda: tmp_path / "shots")
    monkeypatch.setattr(
        renderer, "browse", lambda *a, **k: {"url": "u", "png": picture, "width": 800, "height": 600}
    )

    answer = call(db, act="look")

    assert "png" not in answer
    assert Path(answer["shot"]).read_bytes() == b"\x89PNG not really"
    assert (answer["width"], answer["height"]) == (800, 600)


def test_a_resize_reaches_the_shell_with_its_size(shell, db: Path):
    call(db, act="resize", preset="phone")
    call(db, act="resize", width=1440, ratio="16:10")

    assert [args for _, _, args in shell] == [{"preset": "phone"}, {"width": 1440, "ratio": "16:10"}]


def test_which_tab_is_said_only_when_there_is_more_than_one(monkeypatch, db: Path):
    """With one tab the field is always the same, and it would be paid for on every call."""
    one = {"url": "u", "title": "One", "tab": "t1", "tabs": [{"id": "t1", "title": "One", "url": "u"}]}
    monkeypatch.setattr(renderer, "browse", lambda *a, **k: dict(one))
    assert "tab" not in call(db, act="read")

    two = {**one, "tab": "t2", "tabs": [*one["tabs"], {"id": "t2", "title": "Two", "url": "v"}]}
    monkeypatch.setattr(renderer, "browse", lambda *a, **k: dict(two))
    assert call(db, act="read")["tab"] == "t2"


def test_the_tabs_come_back_one_line_each_with_the_front_one_marked(monkeypatch, db: Path):
    monkeypatch.setattr(
        renderer,
        "browse",
        lambda *a, **k: {
            "url": "v",
            "title": "Two",
            "tab": "t2",
            "tabs": [{"id": "t1", "title": "One", "url": "u"}, {"id": "t2", "title": "", "url": "v"}],
        },
    )

    assert call(db, act="tabs")["tabs"] == ["t1: One — u", "t2 (in front): untitled — v"]


def test_opening_in_a_new_tab_is_asked_for_by_name(shell, db: Path):
    call(db, act="open", url="example.com", new_tab=True)
    call(db, act="switch", tab="t1")
    call(db, act="close")

    assert [(act, args) for act, _, args in shell] == [
        ("open", {"url": "example.com", "new_tab": True, "private": False}),
        ("switch", {"tab": "t1"}),
        ("close", {"tab": ""}),
    ]


def test_new_errors_ride_along_on_any_answer(monkeypatch, db: Path):
    """A 500 behind a page that looks fine is found on the call that caused it, unasked."""
    monkeypatch.setattr(
        renderer, "browse", lambda *a, **k: {"url": "u", "title": "t", "errors": ["POST /api/orders → 500"]}
    )

    assert call(db, act="click", text="Pay")["errors"] == ["POST /api/orders → 500"]


def test_asking_for_errors_when_there_are_none_says_none(monkeypatch, db: Path):
    monkeypatch.setattr(renderer, "browse", lambda *a, **k: {"url": "u", "title": "t", "errors": []})

    assert call(db, act="errors")["errors"].startswith("none")


def test_wait_passes_what_to_wait_for_and_its_answer_comes_back(shell, db: Path, monkeypatch):
    call(db, act="wait", text="Order placed")
    call(db, act="wait", url="/paid", gone=True)
    assert [args for _, _, args in shell] == [
        {"text": "Order placed", "selector": "", "url": "", "gone": False},
        {"text": "", "selector": "", "url": "/paid", "gone": True},
    ]

    monkeypatch.setattr(
        renderer,
        "browse",
        lambda *a, **k: {"url": "u", "title": "t", "found": False, "waited": "did not appear"},
    )
    assert call(db, act="wait", text="x") == {
        "url": "u",
        "title": "t",
        "found": False,
        "waited": "did not appear",
    }


def test_a_private_tab_is_asked_for_by_name(shell, db: Path):
    call(db, act="open", url="example.com", private=True)

    assert shell[0][2] == {"url": "example.com", "new_tab": False, "private": True}


def test_show_waits_for_the_window_and_says_it_opened(db: Path):
    """It parks on the queue a plugin's host effects use, so it inherits the deadline, the
    per-window claim and the wake-on-stop rather than growing a second set of clocks."""

    def the_window_answers() -> None:
        for _ in range(80):
            time.sleep(0.05)
            waiting = calls.pending("c-show", client="a-window", kind="host")
            if waiting:
                one = waiting[0]
                assert (one["plugin"], one["view"]) == (browser_tool.HOST, "browser")
                calls.reply(one["id"], {})
                return

    turn = live_turns.begin("c-show")
    try:
        with session_context.working_in("c-show"):
            threading.Thread(target=the_window_answers, daemon=True).start()
            answer = call(db, act="show")
    finally:
        live_turns.finish(turn)
        calls.release("c-show")
        calls.forget_misses()

    assert answer == {"opened": True}


def test_show_with_nobody_there_says_so_at_once(db: Path):
    with session_context.working_in("c-night"), session_context.nobody_watching():
        answer = call(db, act="show")

    assert "Nobody is here" in answer["error"]


def test_it_is_one_of_kiths_own_tools(shell, db: Path):
    """Registered and run like any other built-in — no install, no plugin prefix."""
    answer = run_tool("browser", {"act": "read"}, db)

    assert answer["ok"] is True
    assert answer["result"]["url"] == "https://example.com/"


def test_one_tool_costs_less_than_the_nine_it_replaced():
    """The plugin's nine command schemas were ~3,900 characters on every request. The number is
    held here so that adding to this tool is a decision with a size attached — it was raised from
    2,000 to 2,600 for tabs, `wait`, `errors`, private tabs and `save_test`."""
    assert len(json.dumps(require("browser").schema())) < 2_600


# --------------------------------------------------------------------------- #
# The skill ships with Kith
# --------------------------------------------------------------------------- #


def test_the_skill_is_bundled_and_teaches_this_tool():
    """Bundled rather than imported from a plugin, so an update can deliver a change to it the
    way it delivers one to `running-a-project`."""
    skill = SERVER / "skills" / "driving-a-browser" / "SKILL.md"
    text = skill.read_text()

    assert text.startswith("---\nname: driving-a-browser\n")
    assert "`browser`" in text
    assert "plugin__browser" not in text


# --------------------------------------------------------------------------- #
# A plugin cannot be a browser any more
# --------------------------------------------------------------------------- #


def manifest(folder: Path, **over) -> Path:
    body = {
        "manifest": 1,
        "id": "tabby",
        "name": "Tabby",
        "version": "0.1.0",
        "description": "A tab.",
        **over,
    }
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "kith.plugin.json").write_text(json.dumps(body))
    return folder


def test_a_plugin_surface_cannot_be_a_browser(tmp_path: Path):
    folder = manifest(tmp_path / "tabby", surfaces=[{"id": "page", "title": "Page", "kind": "web"}])

    with pytest.raises(PluginError, match="built into Kith"):
        parse(folder)


def test_a_surface_that_says_it_is_a_document_still_parses(tmp_path: Path):
    """`document` was the only other kind and is what every surface is, so spelling it out is
    saying something true."""
    folder = manifest(
        tmp_path / "tabby",
        surfaces=[{"id": "page", "title": "Page", "kind": "document", "entry": "ui/page.html"}],
    )

    assert parse(folder).surfaces[0].id == "page"


def test_a_command_that_drives_a_browser_is_refused_by_name(tmp_path: Path):
    folder = manifest(
        tmp_path / "tabby",
        commands=[
            {"name": "open", "title": "Open", "delivery": "view", "does": {"act": "open"}, "model": True}
        ],
    )

    assert any("built into Kith" in why for why in parse(folder).problems())


# --------------------------------------------------------------------------- #
# An install of the old plugin
# --------------------------------------------------------------------------- #


@pytest.fixture
def somewhere_to_install(tmp_path: Path, config_db: Path, monkeypatch):
    monkeypatch.setenv("KITH_PLUGINS_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("KITH_SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setattr("kith.settings.CONFIG_DB_PATH", config_db)
    # Out of the way, not into the real Trash.
    monkeypatch.setattr(installer, "_trash", lambda target: shutil.rmtree(target, ignore_errors=True))
    registry.forget_cache()
    yield
    registry.forget_cache()
    skills_service.resync()


def an_old_browser_plugin(tmp_path: Path, config_db: Path, *, web: bool = True) -> None:
    """The old plugin as it sits on a machine that had it: installed, its skill imported, and a
    manifest that declares a browser surface. Installed as a plain plugin first and then given the
    browser surface, because a manifest with one can no longer be installed at all."""
    source = manifest(tmp_path / "source" / "browser", id="browser", name="Browser")
    skill = source / "skills" / "driving-a-browser"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: driving-a-browser\ndescription: The plugin's copy.\n---\n\nUse plugin__browser__open.\n"
    )
    installer.install(config_db, source)
    if web:
        installed = registry.root() / "browser" / "kith.plugin.json"
        body = json.loads(installed.read_text())
        body["surfaces"] = [{"id": "page", "title": "Browser", "kind": "web"}]
        installed.write_text(json.dumps(body))


def test_an_installed_browser_plugin_is_retired_and_the_bundled_skill_takes_its_place(
    somewhere_to_install, tmp_path: Path, config_db: Path
):
    """Left alone it would be a broken row, and its imported skill would sit in the folder
    teaching tools that no longer exist — and blocking the bundled skill, because the seeder never
    overwrites bytes it did not write."""
    an_old_browser_plugin(tmp_path, config_db)

    assert installer.retire_superseded(config_db) == ["browser"]

    assert registry.rows(config_db)["browser"].get("retiredAt")
    landed = (skills_service.root() / "driving-a-browser" / "SKILL.md").read_text()
    assert landed == (SERVER / "skills" / "driving-a-browser" / "SKILL.md").read_text()


def test_a_plugin_that_is_only_called_browser_is_left_alone(
    somewhere_to_install, tmp_path: Path, config_db: Path
):
    """The kind is checked as well as the id: somebody else's plugin named `browser` is theirs."""
    an_old_browser_plugin(tmp_path, config_db, web=False)

    assert installer.retire_superseded(config_db) == []
    assert not registry.rows(config_db)["browser"].get("retiredAt")
