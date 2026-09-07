"""A plugin surface can be a bundled React app, and the seal does not move to allow it.

The interesting claim in the plugin design is not that a surface can draw — it is that a surface
is an ordinary web page, so **any** library works inside it. That only holds if a bundle can be
got into a document with `default-src 'none'` on it, which means inlined rather than fetched.

`examples/plugins/flowpad` is the proof: React, React DOM and React Flow, bundled to one file and
inlined into the sealed document. The numbers matter as much as the fact — a surface's bytes are
paid on every mount — so they are asserted rather than described, and `MAX_BYTES` stops being the
unmeasured constant the design document flagged.

The two inlining bugs pinned below both had the same shape: **an asset silently not arriving.**
That is the worst failure this subsystem can have, because a surface with no script and no
stylesheet renders as a blank pane with nothing anywhere saying why.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest

from kith.domain.seal import MAX_BYTES, POLICY
from kith.services.plugins import documents, icons, registry
from kith.services.plugins import install as installer

FLOWPAD = Path(__file__).resolve().parents[2] / "examples" / "plugins" / "flowpad"

#: A palette with every token the example's stylesheet maps onto React Flow's own variables.
PALETTE = {
    "bg": "#faf9f7",
    "line": "#e4e1dc",
    "text": "#1c1a17",
    "dim": "#6b6660",
    "accent": "#b4703a",
    "accent-soft": "#f0e2d5",
    "second": "#3a6b58",
    "second-soft": "#dceade",
    "muted": "#f2f0ed",
}


@pytest.fixture
def flowpad(tmp_path: Path, config_db: Path, monkeypatch):
    if not (FLOWPAD / "ui" / "board.js").is_file():
        pytest.skip("the flowpad bundle is not built — run `npm run build` in its folder")
    monkeypatch.setenv("KITH_PLUGINS_DIR", str(tmp_path / "plugins"))
    monkeypatch.setenv("KITH_SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setattr("kith.settings.CONFIG_DB_PATH", config_db)
    registry.forget_cache()
    yield installer.install(config_db, FLOWPAD)
    registry.forget_cache()


def sealed(plugin) -> str:
    return documents.sealed(
        plugin, plugin.surface("board"), PALETTE, icons.named(["layout-grid", "trash"])
    ).decode()


# --------------------------------------------------------------------------- #
# It installs, and the whole bundle arrives
# --------------------------------------------------------------------------- #


def test_the_example_installs_with_no_faults(config_db: Path, tmp_path: Path, monkeypatch):
    if not (FLOWPAD / "ui" / "board.js").is_file():
        pytest.skip("the flowpad bundle is not built")
    monkeypatch.setenv("KITH_PLUGINS_DIR", str(tmp_path / "plugins"))
    monkeypatch.setattr("kith.settings.CONFIG_DB_PATH", config_db)

    assert installer.inspect(FLOWPAD, config_db)["faults"] == []


def test_react_and_react_flow_are_in_the_document(flowpad):
    document = sealed(flowpad)

    # The bundle, and React Flow's own stylesheet, both inlined — not referenced.
    assert "createRoot" in document
    assert ".react-flow" in document
    # And the example's own CSS, which maps React Flow's variables onto the host's tokens so a
    # theme change repaints the graph without remounting the frame.
    assert "--xy-edge-stroke" in document


def test_nothing_at_all_is_left_to_fetch(flowpad):
    """The property the seal rests on. `default-src 'none'` costs a surface nothing only while
    there is nothing it needs to load."""
    document = sealed(flowpad)

    assert re.findall(r"<script[^>]*\bsrc=", document) == []
    assert re.findall(r"<link[^>]*stylesheet", document, re.I) == []
    assert re.findall(r"""<img[^>]*src=["']https?:""", document, re.I) == []


def test_it_arrives_under_the_same_seal_a_canvas_does(flowpad):
    document = sealed(flowpad)

    assert POLICY in document
    assert "default-src 'none'" in POLICY
    # A React app changes nothing about what the frame may do.
    assert "connect-src" not in POLICY


# --------------------------------------------------------------------------- #
# What it costs, measured rather than described
# --------------------------------------------------------------------------- #


def test_the_document_fits_well_inside_the_ceiling(flowpad):
    """`MAX_BYTES` was inherited from the canvas and flagged in the design as unmeasured for
    plugins. This is the measurement: a full React + React Flow surface is about a fifth of it,
    so the ceiling is generous rather than tight — and a plugin that blows it is doing something
    a person should hear about, not being squeezed by a number nobody chose."""
    size = len(sealed(flowpad).encode())

    assert size < MAX_BYTES // 2, f"{size:,} bytes against a {MAX_BYTES:,} ceiling"
    # And it is genuinely large, so this test is measuring the case it claims to.
    assert size > 200_000


def test_it_costs_prompt_tokens_only_for_its_commands(flowpad):
    """The bundle is not prompt cost. A surface's bytes are paid by the browser on mount; what
    rides in every request is the command schemas and the digest allowance, and conflating the
    two would price a React plugin as unaffordable when it is nothing of the kind."""
    assert flowpad.prompt_chars() < 3_000
    assert flowpad.prompt_tokens() < 800


def test_a_surfaces_own_root_is_given_the_full_frame(flowpad):
    """**The bug that produced a blank pane with thirteen nodes in the DOM.**

    Measured in Chromium, because reasoning about it is exactly how it went unnoticed: the header
    laid out at 45px and the graph at **zero**. The chain is ordinary CSS, which is the problem —
    `body` is 100% tall, a mounting div is `height: auto`, and a percentage height inside an
    auto-height parent behaves as auto, so a `flex: 1` child has nothing to grow into.

    Every full-height surface wants this and each one would lose an afternoon to it separately,
    which is what a host stylesheet is for. `:where()` keeps the specificity at zero so a surface
    that wants a short, content-sized root just says so and wins.

    Asserted on the text rather than on a rendered box because there is no layout engine in this
    suite — `ui/scripts/look-at-a-plugin.mjs` is the thing that actually measures it, and this is
    the guard that the rule does not quietly get deleted.
    """
    document = sealed(flowpad)

    assert ":where(body > div:only-of-type) { height: 100%; }" in document
    # And the frame itself is full height, or the rule above has nothing to resolve against.
    assert "html, body { margin: 0; height: 100%; }" in document


# --------------------------------------------------------------------------- #
# The two ways an asset can silently fail to arrive
# --------------------------------------------------------------------------- #


def test_a_module_script_stays_a_module_script():
    """**The foot-gun every React plugin would have hit.** Most bundlers emit ES modules, so an
    ordinary Vite config produces a file with top-level `import`/`export`. Inlined as a classic
    script that is a syntax error, and the surface dies before its first line runs — blank pane,
    nothing in any log the person can reach."""
    root = Path(tempfile.mkdtemp()).resolve()
    (root / "esm.js").write_text("export const a = 1;")
    (root / "classic.js").write_text("var b = 2;")

    out = documents._inline(
        '<script type="module" src="esm.js"></script><script src="classic.js"></script>',
        root,
        root,
    )

    assert '<script type="module">export const a = 1;</script>' in out
    # And a classic script is not promoted, because a module has different scoping rules and
    # silently changing them is its own bug.
    assert "<script>var b = 2;</script>" in out


def test_an_unresolved_root_does_not_silently_drop_everything():
    """The second one, and it is why this file exists rather than a comment.

    The containment check compares against a *resolved* target, so an unresolved `root` fails
    every comparison and drops every asset — an empty surface, no error. On macOS that is the
    ordinary case, not an exotic one: `/var` is a symlink to `/private/var`, so anything under a
    temporary directory hits it. Found by a test that passed `mkdtemp()` straight in.
    """
    unresolved = Path(tempfile.mkdtemp())
    (unresolved / "a.css").write_text("body{color:red}")

    out = documents._inline('<link rel="stylesheet" href="a.css">', unresolved, unresolved)

    assert "<style>body{color:red}</style>" in out


def test_an_asset_outside_the_plugin_is_still_refused():
    """The resolution above must not have widened containment while fixing it."""
    root = Path(tempfile.mkdtemp()).resolve()

    assert documents._inline('<script src="../../../etc/passwd"></script>', root, root) == ""


# --------------------------------------------------------------------------- #
# The commands, which is how he builds the diagram
# --------------------------------------------------------------------------- #


def test_he_builds_a_diagram_a_node_at_a_time(flowpad, db: Path):
    from kith import tools
    from kith.kernel import session_context
    from kith.services.plugins import state

    with session_context.working_in("c-flow"):
        tools.run_tool("plugin__flowpad__set_title", {"title": "Install flow"}, db)
        for step in ("manifest", "review", "grants"):
            assert tools.run_tool(
                "plugin__flowpad__node", {"id": step, "label": step.title(), "kind": "step"}, db
            )["ok"]
        assert tools.run_tool("plugin__flowpad__edge", {"from": "manifest", "to": "review"}, db)["ok"]
        queued = state.read(db, "flowpad")["values"]["pending"]

    # Five calls, five records, in order — the queue the surface folds. Nothing is lost while
    # the tab is closed, which is the whole reason this is a queue.
    assert [one["command"] for one in queued] == ["set_title", "node", "node", "node", "edge"]
    assert [one["seq"] for one in queued] == [1, 2, 3, 4, 5]


def test_a_node_kind_outside_the_declared_set_is_refused_by_name(flowpad, db: Path):
    from kith import tools
    from kith.kernel import session_context

    with session_context.working_in("c-flow"):
        answer = tools.run_tool("plugin__flowpad__node", {"id": "a", "label": "A", "kind": "hexagon"}, db)

    assert answer["ok"] is False
    assert "'kind'" in answer["error"] and "decision" in answer["error"]
