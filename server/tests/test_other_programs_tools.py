"""MCP: tools from other programs, and the rules that keep them from costing anything.

Every test here talks to a real server over a real pipe (`tests/mcp_probe_server.py`). A
mock would agree with whatever the client believes about the protocol, and the protocol is
the whole of what could be wrong: framing, skipping traffic that is not the reply, and the
difference between a tool that fails and a call that fails.

Three properties matter more than the plumbing, and each has its own class below:

* a server can never shadow a built-in — by construction, not by a check;
* the tool block is frozen for a turn, because it is part of the cached prompt prefix and a
  server dying mid-turn would otherwise discard the whole cache;
* secrets configured as environment go out to no client, ever.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from kith.domain.mcp import MCPServer, split_tool_name, tool_name
from kith.services.mcp import manager
from kith.services.mcp.client import MCPError, StdioServer
from kith.tools import run_tool, tool_schemas

PROBE = str(Path(__file__).parent / "mcp_probe_server.py")


def a_server(label: str = "probe") -> MCPServer:
    return MCPServer(label=label, command=sys.executable, args=(PROBE,))


@pytest.fixture(autouse=True)
def nothing_left_running():
    """Every test starts and ends with no server processes.

    Not tidiness — a leaked child holds a pipe, and the module-level registry would carry a
    connected server into the next test's assertions about what is connected.
    """
    manager.disconnect_all()
    yield
    manager.disconnect_all()


@pytest.fixture
def connected(config_db: Path):
    manager.save(config_db, [a_server()])
    assert manager.connect(config_db, 10, 10) == {}, "the probe server would not start"
    return config_db


class TestTheProtocol:
    def test_it_reads_past_a_line_that_is_not_json(self):
        """The probe prints a plain log line before answering `initialize`, because real
        servers do. Taking the first line as the reply would fail on half of them."""
        process = StdioServer(sys.executable, [PROBE], {}, 10)
        try:
            process.start()
            assert process.server_info["name"] == "probe"
        finally:
            process.stop()

    def test_and_past_a_notification_with_no_id(self):
        """A server sends progress and log notifications mid-conversation. Matching on id is
        what stops one of those being returned as a tool's result."""
        process = StdioServer(sys.executable, [PROBE], {}, 10)
        try:
            process.start()
            assert [t["name"] for t in process.list_tools(10)][:2] == ["add", "boom"]
        finally:
            process.stop()

    def test_a_tool_that_fails_is_not_a_call_that_fails(self):
        """MCP reports a tool's own refusal as `isError` on a *successful* response. Treating
        that as a transport error would turn "the tool said no" into "the server is broken"."""
        process = StdioServer(sys.executable, [PROBE], {}, 10)
        try:
            process.start()
            assert process.call("add", {"a": 2, "b": 3}, 10) == {"content": "5", "isError": False}
            failed = process.call("boom", {}, 10)
            assert failed["isError"] is True and "went wrong" in failed["content"]
        finally:
            process.stop()

    def test_an_unknown_tool_is_a_protocol_error(self):
        process = StdioServer(sys.executable, [PROBE], {}, 10)
        try:
            process.start()
            with pytest.raises(MCPError, match="no tool called"):
                process.call("nope", {}, 10)
        finally:
            process.stop()

    def test_a_server_that_never_answers_times_out_rather_than_hanging(self):
        """`slow` sleeps for thirty seconds. A blocking readline cannot be interrupted, so
        without the watchdog this would hold a whole turn."""
        process = StdioServer(sys.executable, [PROBE], {}, 10)
        try:
            process.start()
            with pytest.raises(MCPError, match="no reply within"):
                process.call("slow", {}, 1)
        finally:
            process.stop()

    def test_a_command_that_does_not_exist_says_so(self):
        process = StdioServer("definitely-not-a-real-command", [], {}, 5)
        with pytest.raises(MCPError, match="could not start"):
            process.start()


class TestNothingCanShadowABuiltIn:
    """The probe ships a tool called `shell` on purpose."""

    def test_the_built_in_still_wins(self, connected, db: Path):
        result = run_tool("shell", {"command": "echo untouched"}, db)
        assert result["ok"] is True
        assert "untouched" in result["result"]["output"]

    def test_and_the_server_version_is_reachable_under_its_own_name(self, connected, db: Path):
        assert run_tool("mcp__probe__shell", {}, db)["result"] == "not the real shell"

    def test_the_namespace_is_what_makes_it_impossible(self):
        """Not a collision check that someone could forget to write. A built-in is never
        spelled `mcp__…__…`, so the two sets cannot intersect."""
        assert tool_name("probe", "shell") == "mcp__probe__shell"
        assert split_tool_name("mcp__probe__shell") == ("probe", "shell")

    def test_a_tool_name_containing_underscores_survives_the_round_trip(self):
        """`mcp__github__create_pull_request` is one server and one tool, not a label of
        `github__create`. Splitting on the last separator would route it nowhere."""
        assert split_tool_name(tool_name("github", "create_pull_request")) == (
            "github",
            "create_pull_request",
        )

    @pytest.mark.parametrize("name", ["shell", "read_file", "mcp__", "mcp__x", "mcp____y", ""])
    def test_anything_that_is_not_ours_splits_to_nothing(self, name):
        assert split_tool_name(name) == ("", "")


class TestTheToolBlockIsFrozenForATurn:
    """It is part of the cached prompt prefix. A block that changes mid-turn changes the
    prefix, and the next round pays full price for the entire prompt."""

    def test_a_snapshot_survives_the_server_dying(self, connected):
        before = manager.snapshot()
        assert [s["function"]["name"] for s in before] == [
            "mcp__probe__add",
            "mcp__probe__boom",
            "mcp__probe__shapeless",
            "mcp__probe__shell",
            "mcp__probe__slow",
        ]

        manager.disconnect_all()

        # The turn holds its own list; that is the point of taking one.
        assert len(before) == 5

    def test_and_the_call_then_fails_readably_instead(self, connected, db: Path):
        manager.snapshot()
        manager.disconnect_all()
        failed = run_tool("mcp__probe__add", {"a": 1, "b": 1}, db)
        assert failed["ok"] is False
        assert "not connected" in failed["error"]
        assert "Carry on without it" in failed["error"] or "carry on" in failed["error"].lower()

    def test_the_order_is_stable(self, connected):
        """An unstable order is a different byte sequence, which is a cache miss on the whole
        prefix for no reason at all."""
        assert manager.snapshot() == manager.snapshot()

    def test_mcp_tools_are_passed_in_rather_than_fetched(self, connected, db: Path):
        """`tool_schemas` runs every round. If it asked the manager itself, a server dying
        would shrink the block mid-turn — which is exactly what the snapshot prevents."""
        snapshot = manager.snapshot()
        assert len(tool_schemas(mcp=snapshot)) == len(tool_schemas()) + len(snapshot)
        # Without being handed one, it contributes nothing, whatever is connected.
        assert len(tool_schemas()) == len(tool_schemas(mcp=None))


class TestTheSchemasHandedToTheModel:
    def test_a_tool_keeps_its_own_input_schema(self, connected):
        add = next(s for s in manager.snapshot() if s["function"]["name"] == "mcp__probe__add")
        assert add["function"]["parameters"]["required"] == ["a", "b"]
        assert set(add["function"]["parameters"]["properties"]) == {"a", "b"}

    def test_an_unusable_schema_becomes_an_empty_object(self, connected):
        """A malformed schema is a 400 on the *whole* request, so one bad tool would take
        down every turn rather than only itself."""
        odd = next(s for s in manager.snapshot() if s["function"]["name"] == "mcp__probe__shapeless")
        assert odd["function"]["parameters"] == {"type": "object", "properties": {}, "required": []}

    def test_the_description_says_which_server_it_came_from(self, connected):
        add = next(s for s in manager.snapshot() if s["function"]["name"] == "mcp__probe__add")
        assert add["function"]["description"].startswith("[probe]")


class TestCallingThroughTheLoop:
    def test_a_result_arrives_in_the_envelope_the_loop_speaks(self, connected, db: Path):
        assert run_tool("mcp__probe__add", {"a": 2, "b": 3}, db) == {"ok": True, "result": "5"}

    def test_a_tool_that_refuses_is_reported_as_a_refusal(self, connected, db: Path):
        failed = run_tool("mcp__probe__boom", {}, db)
        assert failed["ok"] is False and "went wrong" in failed["error"]

    def test_a_tool_no_server_has_is_an_unknown_tool(self, connected, db: Path):
        assert run_tool("mcp__probe__ghost", {}, db)["ok"] is False

    def test_a_server_that_was_never_configured_is_too(self, connected, db: Path):
        assert run_tool("mcp__ghost__add", {}, db)["ok"] is False

    def test_the_allow_list_still_gates_them(self, connected, db: Path):
        """An MCP tool is a tool. The landing reserve and the per-mode sets have to apply to
        it, or adding a server is a way around every narrowing in the loop."""
        refused = run_tool("mcp__probe__add", {"a": 1, "b": 1}, db, allow={"journal"})
        assert refused["ok"] is False and "not available" in refused["error"]


class TestConfiguration:
    def test_trying_a_server_does_not_save_it(self, config_db: Path):
        manager.probe(a_server(), 10, 10)
        assert manager.configured(config_db) == []

    def test_a_label_is_a_namespace_so_it_has_to_be_unique(self, config_db: Path):
        with pytest.raises(ValueError, match="already a server"):
            manager.save(config_db, [a_server(), a_server()])

    @pytest.mark.parametrize("label", ["", "Has Caps", "with space", "a__b", "x" * 40])
    def test_a_label_that_would_break_a_tool_name_is_refused(self, config_db: Path, label: str):
        with pytest.raises(ValueError):
            manager.save(config_db, [MCPServer(label=label, command="echo")])

    def test_a_server_with_no_command_is_refused(self, config_db: Path):
        with pytest.raises(ValueError, match="no command"):
            manager.save(config_db, [MCPServer(label="x", command="  ")])

    def test_a_disabled_server_is_not_started(self, config_db: Path):
        manager.save(
            config_db, [MCPServer(label="probe", command=sys.executable, args=(PROBE,), enabled=False)]
        )
        assert manager.connect(config_db, 10, 10) == {}
        assert manager.running() == {}
        assert manager.snapshot() == []

    def test_a_server_that_will_not_start_is_reported_not_raised(self, config_db: Path):
        """One broken server must not stop the others being saved and started."""
        manager.save(config_db, [MCPServer(label="broken", command="definitely-not-real")])
        trouble = manager.connect(config_db, 5, 5)
        assert "broken" in trouble

    def test_removing_one_stops_it(self, connected):
        assert "probe" in manager.running()
        manager.forget(connected, "probe")
        assert manager.running() == {}

    def test_connecting_twice_leaves_the_first_process_alone(self, connected):
        """Reconnecting a live server would swap its tool list under any turn using it."""
        before = manager.running()["probe"]
        assert manager.connect(connected, 10, 10) == {}
        assert manager.running()["probe"] == before

    def test_the_round_trip_through_storage_keeps_everything(self, config_db: Path):
        original = MCPServer(
            label="files", command="npx", args=("-y", "@x/files"), env={"TOKEN": "s3cret"}, enabled=False
        )
        manager.save(config_db, [original])
        assert manager.configured(config_db) == [original]

    def test_a_corrupt_list_reads_as_empty_rather_than_raising(self, config_db: Path):
        from kith.infra.db import config_store

        config_store.update_settings(config_db, {manager.SERVERS_KEY: "not json"})
        assert manager.configured(config_db) == []


class TestSecretsStayIn:
    def test_the_public_shape_reports_names_and_never_values(self):
        server = MCPServer(label="files", command="npx", env={"GITHUB_TOKEN": "ghp_realsecret"})
        public = json.dumps(server.public())
        assert "GITHUB_TOKEN" in public
        assert "ghp_realsecret" not in public, "a settings page would leak this to anything reading it"

    def test_but_storage_keeps_them_or_the_server_cannot_start(self):
        assert MCPServer(label="f", command="x", env={"K": "v"}).stored()["env"] == {"K": "v"}


class TestSwitchingOneOffActuallyStopsIt:
    """The switch exists so you can stop paying for a server without losing its setup, and
    the settings page says exactly that. It was a flag and nothing else.

    Measured before the fix: `enabled: false`, `connected: true`, five tools still in the
    snapshot, and the child process still alive. So switching off cost the same as leaving it
    on, which is the one thing the control must never do.
    """

    def disabled(self) -> MCPServer:
        return MCPServer(label="probe", command=sys.executable, args=(PROBE,), enabled=False)

    def test_the_process_stops(self, connected):
        assert manager.running(), "the fixture should have it running"
        manager.save(connected, [self.disabled()])
        assert manager.running() == {}, "switching off left the server running"

    def test_and_its_tools_leave_the_snapshot(self, connected):
        assert manager.snapshot(), "the fixture should have offered tools"
        manager.save(connected, [self.disabled()])
        assert manager.snapshot() == [], "a switched-off server was still costing tokens"

    def test_but_the_configuration_survives(self, connected):
        """Off is not removed — that is the entire difference between the switch and Remove."""
        manager.save(connected, [self.disabled()])
        remaining = manager.configured(connected)
        assert [s.label for s in remaining] == ["probe"]
        assert remaining[0].enabled is False

    def test_and_switching_it_back_on_brings_it_up(self, connected):
        manager.save(connected, [self.disabled()])
        manager.save(connected, [a_server()])
        assert manager.connect(connected, 10, 10) == {}
        assert "probe" in manager.running()

    def test_connect_reconciles_rather_than_only_starting(self, connected):
        """A config changed by any route must converge, so no caller has to remember to tidy
        up. Written directly to storage here, bypassing `save` entirely."""
        from kith.infra.db import config_store

        config_store.update_settings(connected, {manager.SERVERS_KEY: json.dumps([self.disabled().stored()])})
        assert manager.running(), "still running, since nothing has reconciled yet"

        manager.connect(connected, 10, 10)

        assert manager.running() == {}
