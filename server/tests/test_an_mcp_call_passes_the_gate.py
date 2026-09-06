"""An MCP tool call goes through the permission gate, and leaves a trace when it runs.

This is the hole the plugin work opened with. `run_tool` is described in its own docstring as
"the single chokepoint for every tool call, and so the place a permission layer belongs when
this runs unsandboxed" — and the MCP branch sat *below* the `try` that catches
`permissions.Denied`, calling `manager.run` directly. Two things followed:

* **No gate.** A built-in tool that reaches outside the workspace raises `Denied`, which
  becomes the `{ok, error, permission}` envelope the interface draws an Allow button on. An
  MCP server — a subprocess started with the whole environment and no confinement — was the
  one actor in the process with no gate in front of it at all.
* **No trace.** `touched.record` sits under that same branch, so an MCP server that read a
  file left nothing in the manifest that stops him reading it again. The staleness machinery
  was structurally blind to exactly the tools a plugin brings.

What is gated is the *program*, not the call: Kith cannot read an opaque `tools/call` and say
which path it is about to open, so a per-call path check would inspect nothing while looking
exactly like the checks that inspect everything. These tests pin the honest unit — may this
program act — and pin that the refusal is shaped like every other refusal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith import tools
from kith.domain.mcp import MCPServer
from kith.infra import permissions
from kith.services import touched
from kith.services.mcp import manager


@pytest.fixture(autouse=True)
def a_configured_server(config_db: Path, monkeypatch):
    """One server called `notes`, and nothing actually spawned."""
    monkeypatch.setattr("kith.settings.CONFIG_DB_PATH", config_db)
    manager.save(config_db, [MCPServer(label="notes", command="npx", args=("-y", "notes-mcp"))])
    monkeypatch.setattr(manager, "run", lambda *a, **k: {"ok": True, "result": "two circulars"})
    permissions.revoke_all()
    permissions.set_mode("ask")
    yield
    permissions.revoke_all()
    permissions.set_mode("ask")


def test_an_ungranted_server_is_refused_with_an_allow_button(db: Path):
    answer = tools.run_tool("mcp__notes__list", {}, db)

    assert answer["ok"] is False
    # The envelope, not a bare string. This is what puts an Allow button on the tool result
    # instead of sending someone hunting for a settings page while he waits.
    assert answer["permission"]["kind"] == "plugin"
    assert "notes" in answer["permission"]["what"]


def test_a_granted_server_runs(db: Path, config_db: Path):
    permissions.grant_now(manager.grant_signature(config_db, "notes"), standing=False)

    answer = tools.run_tool("mcp__notes__list", {}, db)

    assert answer == {"ok": True, "result": "two circulars"}


def test_the_grant_is_specific_to_what_gets_spawned(db: Path, config_db: Path):
    """Changing the command line is an ungranted signature, with no upgrade-consent code."""
    permissions.grant_now(manager.grant_signature(config_db, "notes"), standing=False)
    manager.save(config_db, [MCPServer(label="notes", command="npx", args=("-y", "notes-mcp@9"))])

    assert tools.run_tool("mcp__notes__list", {}, db)["ok"] is False


def test_a_call_that_ran_reaches_the_touched_manifest(db: Path, config_db: Path, monkeypatch):
    """The bookkeeping every built-in has had and MCP never did."""
    seen: list[str] = []
    monkeypatch.setattr(touched, "record", lambda path, name, args, result: seen.append(name))
    permissions.grant_now(manager.grant_signature(config_db, "notes"), standing=False)

    tools.run_tool("mcp__notes__list", {}, db)

    assert seen == ["mcp__notes__list"]


def test_a_refused_call_records_nothing(db: Path, monkeypatch):
    """A refused call did not happen, and recording that it did is a lie that suppresses the
    read he still needs to make — the same rule the built-in branch already follows."""
    seen: list[str] = []
    monkeypatch.setattr(touched, "record", lambda path, name, args, result: seen.append(name))

    tools.run_tool("mcp__notes__list", {}, db)

    assert seen == []


def test_a_server_that_is_gone_still_answers_readably(db: Path, config_db: Path):
    """A turn holds its tool snapshot after a server is removed, so he *will* call a tool whose
    server has gone. Prompting for a program that no longer exists would replace a sentence he
    can act on with a dialog nobody can answer usefully."""
    manager.save(config_db, [])

    answer = tools.run_tool("mcp__notes__list", {}, db)

    assert answer["ok"] is True  # manager.run is stubbed; the point is that no prompt was raised
    assert "permission" not in answer


def test_bypass_mode_does_not_gate(db: Path):
    permissions.set_mode("bypass")
    assert tools.run_tool("mcp__notes__list", {}, db)["ok"] is True


def test_an_unknown_tool_is_still_an_unknown_tool(db: Path):
    """Restructuring the branch must not turn a typo into a permission prompt."""
    answer = tools.run_tool("definitely_not_a_tool", {}, db)
    assert answer["ok"] is False
    assert "unknown tool" in answer["error"]
