"""The example plugin, installed and driven the way a person and the model would drive it.

Every other plugin test builds a manifest inline. This one installs `examples/plugins/sketchpad`
— the actual folder someone would be handed — and exercises the loop it exists to demonstrate:

    he calls `draw`  ->  a record lands in the plugin's store
    core pushes the store into the sealed frame
    the frame folds the record into its own list and writes the list back
    the digest carries what happened into his next turn

The frame's half runs in a browser and is not simulated here; what is asserted is every seam it
touches, plus the two properties the seal rests on — that the served document has nothing left to
fetch, and that it carries the same policy a canvas does.

**This is the file that fails if the example rots.** A shipped example nobody runs is a worked
example that stops working, and it is the first thing anyone reads.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kith import tools
from kith.domain.seal import FRAME_SANDBOX, POLICY
from kith.kernel import session_context
from kith.services.plugins import commands, documents, icons, registry, state
from kith.services.plugins import install as installer

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "plugins" / "sketchpad"


@pytest.fixture(autouse=True)
def sketchpad(tmp_path: Path, config_db: Path, db: Path, monkeypatch):
    if not EXAMPLE.is_dir():
        pytest.skip("the example plugins are not in this checkout")
    monkeypatch.setenv("KITH_PLUGINS_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("KITH_SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setattr("kith.settings.CONFIG_DB_PATH", config_db)
    registry.forget_cache()
    installer.install(config_db, EXAMPLE)
    yield registry.get(config_db, "sketchpad")
    registry.forget_cache()


def in_a_conversation(conversation_id: str = "c-sketch"):
    return session_context.working_in(conversation_id)


# --------------------------------------------------------------------------- #
# It installs, and contributes all four kinds of thing
# --------------------------------------------------------------------------- #


def test_the_example_installs_with_no_faults(config_db: Path):
    report = installer.inspect(EXAMPLE, config_db)
    assert report["faults"] == []


def test_it_contributes_a_tab_a_skill_and_three_commands(sketchpad, config_db: Path):
    assert [s.id for s in sketchpad.surfaces] == ["board"]
    assert sketchpad.skills == ("sketching",)
    assert sorted(c.name for c in sketchpad.commands) == ["clear", "draw", "set_title"]

    from kith.services import skills as skills_service

    assert "sketching" in [s.name for s in skills_service.installed()]


def test_its_commands_reach_the_model_under_their_own_namespace(config_db: Path):
    offered = [s["function"]["name"] for s in registry.tool_schemas(config_db)]
    assert offered == [
        "plugin__sketchpad__clear",
        "plugin__sketchpad__draw",
        "plugin__sketchpad__set_title",
    ]


def test_it_costs_what_the_review_said_it_would(sketchpad, config_db: Path):
    """The number on the install screen is the number that lands in the prompt.

    Schemas plus the digest allowance, because the digest is the other thing that rides on every
    request — and quoting only the schemas would price a plugin at less than it costs the moment
    somebody switches its line on.
    """
    schemas = sum(len(json.dumps(one)) for one in registry.tool_schemas(config_db))
    assert sketchpad.prompt_chars() == schemas + state.DIGEST_CHARS_PER_PLUGIN
    # And the tokens figure is the chars figure in this repo's own units, not a second estimate.
    assert sketchpad.prompt_tokens() == int(sketchpad.prompt_chars() / 3.7)


# --------------------------------------------------------------------------- #
# The loop
# --------------------------------------------------------------------------- #


def test_he_draws_and_it_lands_in_the_store(db: Path):
    with in_a_conversation():
        answer = tools.run_tool(
            "plugin__sketchpad__draw",
            {"shape": "rect", "x": 100, "y": 120, "w": 180, "h": 60, "label": "Intake", "colour": "accent"},
            db,
        )
        assert answer["ok"] is True
        held = state.read(db, "sketchpad")["values"]["pending"]

    assert held["command"] == "draw"
    assert held["args"]["label"] == "Intake"
    # Sequenced, so a surface can tell a new record from one it has already folded in — without
    # it, every repaint would add the same shape again.
    assert held["seq"] == 1


def test_two_calls_in_one_round_get_different_sequences(db: Path):
    with in_a_conversation():
        for x in (100, 400):
            tools.run_tool("plugin__sketchpad__draw", {"shape": "circle", "x": x, "y": 200}, db)
        assert state.read(db, "sketchpad")["values"]["pending"]["seq"] == 2


def test_arguments_are_coerced_to_the_declared_shape(db: Path):
    with in_a_conversation():
        tools.run_tool(
            "plugin__sketchpad__draw",
            # Out of range, and a string where an integer was declared.
            {"shape": "rect", "x": "50", "y": 9_999, "colour": "accent"},
            db,
        )
        args = state.read(db, "sketchpad")["values"]["pending"]["args"]

    assert args["x"] == 50
    assert args["y"] == 1000


def test_a_value_outside_the_declared_choices_is_refused_by_name(db: Path):
    with in_a_conversation():
        answer = tools.run_tool("plugin__sketchpad__draw", {"shape": "hexagon", "x": 10, "y": 10}, db)
    # Named, so he can fix it in one round rather than guessing.
    assert answer["ok"] is False
    assert "'shape'" in answer["error"] and "rect" in answer["error"]


def test_a_missing_required_argument_is_refused_by_name(db: Path):
    with in_a_conversation():
        answer = tools.run_tool("plugin__sketchpad__draw", {"shape": "rect"}, db)
    assert answer["ok"] is False
    assert "'x'" in answer["error"]


def test_the_surface_writes_back_and_he_sees_it_next_turn(db: Path, config_db: Path):
    """The half he cannot watch happen: a person drags a shape, and the digest carries it."""
    installer.set_digest(config_db, "sketchpad", True)
    with in_a_conversation():
        # What `window.kith.state.set(...)` results in, once the renderer has relayed it.
        state.write(
            db,
            "sketchpad",
            {
                "shapes": [{"shape": "rect", "x": 140, "y": 120}],
                "note": "moved Intake",
                "title": "Intake flow",
            },
            writer="surface",
        )

    line = state.digest(db, config_db, "c-sketch", None)

    assert "Sketchpad — title: Intake flow" in line
    assert "note: moved Intake" in line
    # `shapes` is declared in the digest list but holds a list, and structure is skipped rather
    # than flattened — a plugin does not get to put a paragraph in the prompt dressed as a label.
    assert "shape" not in line.split("note:")[0].replace("Sketchpad", "")


def test_he_can_read_the_whole_store_when_the_line_is_not_enough(db: Path):
    with in_a_conversation():
        state.write(db, "sketchpad", {"shapes": [{"shape": "circle", "x": 1, "y": 2}]}, writer="surface")
        answer = tools.run_tool("plugin_state", {"plugin": "sketchpad"}, db)

    assert answer["result"]["values"]["shapes"][0]["shape"] == "circle"


# --------------------------------------------------------------------------- #
# The document, and the seal it is served under
# --------------------------------------------------------------------------- #


def test_the_document_has_nothing_left_to_fetch(sketchpad):
    """The whole reason the seal does not have to change. Every asset is already inlined, so
    `default-src 'none'` costs the surface nothing and the policy stays as strict as a canvas's."""
    surface = sketchpad.surface("board")
    document = documents.sealed(sketchpad, surface, {"bg": "#fff", "text": "#000"}, {}).decode()

    assert "<script src" not in document
    assert 'rel="stylesheet"' not in document
    # Nothing *loadable* points outward. The SVG namespace literal is a remote-looking URL that
    # is never fetched, so the assertion is about references a browser would act on rather than
    # about the string.
    for attribute in ('src="http', "src='http", 'href="http', "href='http"):
        assert attribute not in document


def test_the_document_carries_the_seal_and_the_bridge_in_order(sketchpad):
    surface = sketchpad.surface("board")
    document = documents.sealed(
        sketchpad, surface, {"bg": "#fff"}, icons.named(["palette", "trash"])
    ).decode()

    # The policy first: a CSP `<meta>` governs only what follows it.
    assert document.index(POLICY) < document.index("window.kith")
    # And the bridge before the plugin's own code, or a page calling `kith.render` from
    # top-level would find nothing there — a failure that reads as broken rather than misordered.
    assert document.index("window.kith=") < document.index("Sketchpad — a plugin surface")


def test_the_icons_it_named_are_kiths_own_bytes(sketchpad):
    surface = sketchpad.surface("board")
    document = documents.sealed(sketchpad, surface, {}, icons.named(["trash"])).decode()

    # A plugin names an icon; it never supplies one. So a surface's iconography is the app's.
    assert 'id="icon-trash"' in document
    assert icons.ICONS["trash"] in document


def test_the_seal_is_not_widened_for_it():
    """Stated as a test rather than as a comment, because "we did not widen the seal" is the one
    claim in this design that is worth being able to check mechanically."""
    assert FRAME_SANDBOX == "allow-scripts"
    assert "default-src 'none'" in POLICY
    assert "connect-src" not in POLICY


def test_a_surface_outside_its_folder_is_refused(sketchpad, tmp_path: Path):
    from dataclasses import replace

    from kith.domain.plugins import PluginError

    escaping = replace(sketchpad.surface("board"), entry="../../../../etc/hosts")
    with pytest.raises(PluginError, match="outside the plugin's folder"):
        documents.resolve_entry(sketchpad, escaping)


# --------------------------------------------------------------------------- #
# Lifecycle, from the model's side
# --------------------------------------------------------------------------- #


def test_switching_it_off_takes_its_commands_away(db: Path, config_db: Path):
    installer.set_enabled(config_db, "sketchpad", False)
    registry.forget_cache()

    assert registry.tool_schemas(config_db) == []
    with in_a_conversation():
        answer = tools.run_tool("plugin__sketchpad__draw", {"shape": "rect", "x": 1, "y": 1}, db)

    # Not "unknown tool", which would tell him he invented a tool he was handed two rounds ago.
    assert answer["ok"] is False
    assert "not installed" in answer["error"]


def test_a_command_that_needs_the_tab_says_so_rather_than_failing_oddly(db: Path, config_db: Path):
    from dataclasses import replace

    plugin = registry.get(config_db, "sketchpad")
    assert plugin is not None
    declared = plugin.command("draw")
    assert declared is not None
    surface_command = replace(declared, delivery="surface", surface="board")

    with in_a_conversation():
        answer = commands.dispatch(db, config_db, plugin, surface_command, {"shape": "rect", "x": 1, "y": 1})

    assert answer["code"] == "surface_not_open"
    assert "Sketchpad" in answer["error"]
