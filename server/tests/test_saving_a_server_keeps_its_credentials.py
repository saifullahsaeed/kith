"""Editing one MCP server does not erase every other server's API token.

This is a regression test for a bug that was live and total. `MCPServer.public()` reports
environment *names* and never values — correct, and the reason a settings page cannot send
back what it never received. So `mcp-servers.tsx` sends `env: {}` for every row it is not
editing, under a comment promising "The server keeps what it has for a label it already
knows; this only ever adds."

`manager.save` did not. It wrote `[s.stored() for s in servers]` with no read of what was
already stored, and the settings page only ever edits this list by PUTting the whole thing.
So switching one server off, or removing an unrelated one, silently wiped the credentials of
every configured server. Nothing failed at the time; the next connect started each server
with no token and the remote's auth error looked like the remote's fault.

The merge is on the server rather than in the client on purpose — any client gets it, and the
comment that was already describing this behaviour becomes true.
"""

from __future__ import annotations

from pathlib import Path

from kith.domain.mcp import MCPServer
from kith.services.mcp import manager


def _saved(config_db: Path) -> dict[str, dict[str, str]]:
    return {s.label: s.env for s in manager.configured(config_db)}


def test_a_row_that_sends_no_environment_keeps_the_one_it_has(config_db: Path):
    manager.save(config_db, [MCPServer(label="notes", command="npx", env={"TOKEN": "secret"})])

    # What the settings page actually sends when you toggle a switch: every row, no values.
    manager.save(config_db, [MCPServer(label="notes", command="npx", env={}, enabled=False)])

    assert _saved(config_db)["notes"] == {"TOKEN": "secret"}


def test_editing_one_server_does_not_touch_another(config_db: Path):
    manager.save(
        config_db,
        [
            MCPServer(label="notes", command="npx", env={"NOTES_TOKEN": "n"}),
            MCPServer(label="github", command="npx", env={"GITHUB_TOKEN": "g"}),
        ],
    )

    # Removing `notes` re-sends `github` with no values, because that is all the page has.
    manager.save(config_db, [MCPServer(label="github", command="npx", env={})])

    assert _saved(config_db) == {"github": {"GITHUB_TOKEN": "g"}}


def test_a_supplied_value_still_wins(config_db: Path):
    """Merging must not make a value un-editable — typing a new token has to replace it."""
    manager.save(config_db, [MCPServer(label="notes", command="npx", env={"TOKEN": "old"})])
    manager.save(config_db, [MCPServer(label="notes", command="npx", env={"TOKEN": "new"})])

    assert _saved(config_db)["notes"] == {"TOKEN": "new"}


def test_an_empty_value_removes_the_key(config_db: Path):
    """The one thing a merge would otherwise make impossible, so it needs an explicit spelling."""
    manager.save(config_db, [MCPServer(label="notes", command="npx", env={"TOKEN": "old"})])
    manager.save(config_db, [MCPServer(label="notes", command="npx", env={"TOKEN": ""})])

    assert _saved(config_db)["notes"] == {}


def test_a_new_server_is_unaffected_by_the_merge(config_db: Path):
    manager.save(config_db, [MCPServer(label="fresh", command="npx", env={"A": "1"})])
    assert _saved(config_db)["fresh"] == {"A": "1"}
