"""Two parsers on the boundary, and the direction each of them used to be wrong in.

`Reach` is what a plugin's subprocess is allowed to touch. Kith does not check a server against
it — it *compiles* it into a sandbox profile, so a manifest that lies is inert. Which means the
parse is the boundary: whatever this file turns a manifest into is what the kernel enforces, and
nothing downstream gets a second opinion.

Both bugs here are the same shape. A parser met a value it did not recognise and resolved it,
and both times it resolved it in the direction of more access:

* `network` was `str(value).lower() != "none"`. Every value that is not the exact string `none`
  granted the network — including `false`, which is how anybody who had not read the schema
  would write it, and which `str()` turns into the string `"False"`.
* the unpinned-runner guard asked whether an argument contained an `@`. A *scope* contains an
  `@`, so `npx -y @acme/notes` — a package with no version at all, which is the shape most
  published MCP servers take — counted as pinned. That is the one case the guard exists for.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kith.domain.plugins import PluginError, parse

MANIFEST = {
    "manifest": 1,
    "id": "notes",
    "name": "Notes",
    "version": "1.0.0",
}


def a_manifest(tmp_path: Path, **server) -> Path:
    source = tmp_path / "notes"
    source.mkdir(parents=True, exist_ok=True)
    (source / "kith.plugin.json").write_text(json.dumps({**MANIFEST, "server": server}))
    return source


# --------------------------------------------------------------------------- #
# The network
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("declared, granted", [("any", True), ("none", False), (True, True), (False, False)])
def test_what_a_manifest_can_say_about_the_network(tmp_path: Path, declared, granted):
    plugin = parse(a_manifest(tmp_path, command="node", reach={"network": declared}))

    assert plugin.server is not None
    assert plugin.server.reach.network is granted


def test_saying_false_means_no_network(tmp_path: Path):
    """**The bug, on its own, because it is the one somebody would actually write.**

    `"network": false` granted full network access. Not through an exotic value — through the
    obvious one.
    """
    plugin = parse(a_manifest(tmp_path, command="node", reach={"network": False}))

    assert plugin.server is not None
    assert plugin.server.reach.network is False


def test_a_value_nobody_recognises_is_refused_rather_than_resolved(tmp_path: Path):
    """Loud, and refused rather than defaulted either way.

    Defaulting open hands out access nobody wrote down. Defaulting closed produces a server that
    hangs on first start with nothing on any screen saying why. Refusing is the only one of the
    three that somebody can act on.
    """
    with pytest.raises(PluginError, match=r"server\.reach\.network"):
        parse(a_manifest(tmp_path, command="node", reach={"network": "sometimes"}))


def test_saying_nothing_is_still_the_open_default(tmp_path: Path):
    """Unchanged, and stated out loud here because it is the surprising half: a plugin that
    declares no reach at all gets the network, and the review screen says so in those words."""
    plugin = parse(a_manifest(tmp_path, command="node"))

    assert plugin.server is not None
    assert plugin.server.reach.network is True


# --------------------------------------------------------------------------- #
# The runner pin
# --------------------------------------------------------------------------- #


def unpinned(tmp_path: Path, *args: str) -> bool:
    plugin = parse(a_manifest(tmp_path, command="npx", args=list(args)))
    assert plugin.server is not None
    return any("downloads its package" in one for one in plugin.server.problems())


def test_a_scope_is_not_a_version(tmp_path: Path):
    """**The bug.** `@acme/notes` has an `@` in it and no version anywhere."""
    assert unpinned(tmp_path, "-y", "@acme/notes") is True


@pytest.mark.parametrize("arg", ["notes", "-y", "@acme/notes"])
def test_what_is_still_unpinned(tmp_path: Path, arg):
    assert unpinned(tmp_path, "-y", arg) is True


@pytest.mark.parametrize("arg", ["notes@1.2.3", "@acme/notes@1.2.3", "notes==1.2.3"])
def test_what_counts_as_pinned(tmp_path: Path, arg):
    assert unpinned(tmp_path, "-y", arg) is False


def test_a_command_that_is_not_a_runner_is_not_asked_to_pin_anything(tmp_path: Path):
    plugin = parse(a_manifest(tmp_path, command="node", args=["server.js"]))

    assert plugin.server is not None
    assert plugin.server.problems() == []
