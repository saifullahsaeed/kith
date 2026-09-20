"""A tool call that failed is written down as one.

`turn_log` recorded which tools ran and never whether they worked, and `run_tool`'s own
docstring says it "always returns a dict, never raises" — so from the outside a tool that fails
every single call is indistinguishable from one that works perfectly.

That is not hypothetical. Measured by hand across 120 conversation transcripts on 2026-09-20:
`edit_files`, the second most-used tool in the system, failed **682 of 2,392 calls — 29%** —
and 566 of those were one fixable mistake that had been live for weeks. Finding it meant
parsing every transcript on disk, because the flight recorder could not answer the question.

Two levels have to be checked, and the second is the one that hides things. `run_tool` wraps a
handler's return as `{"ok": True, "result": …}`, and several handlers report their own failures
*inside* that — `edit_files` returns `{"error": "pass `edits` as a list…"}` with `ok` still
true. Counting only the envelope would have missed exactly the class of failure this exists to
surface.
"""

from __future__ import annotations

from pathlib import Path

from kith.domain import tooling
from kith.infra.db import repositories as repo
from kith.infra.db.connection import apply_migrations, connect
from kith.infra.db.migrations import _migrations


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "agent.db"
    conn = connect(path)
    try:
        apply_migrations(conn, _migrations())
    finally:
        conn.close()
    return path


class TestTellingAFailureFromASuccess:
    def test_a_plain_success_is_not_a_failure(self):
        assert not tooling.call_failed({"ok": True, "result": {"wrote": "a.py"}})

    def test_a_refused_envelope_is(self):
        assert tooling.call_failed({"ok": False, "error": "no such tool"})

    def test_an_error_reported_inside_a_successful_envelope_is(self):
        """The one that matters. `edit_files` returns its own validation failures this way."""
        assert tooling.call_failed({"ok": True, "result": {"error": "edit 1 has no path"}})

    def test_a_handler_saying_ok_false_inside_is(self):
        assert tooling.call_failed({"ok": True, "result": {"ok": False, "error": "refused"}})

    def test_a_result_that_merely_mentions_errors_is_not(self):
        """`run_tests` reports failing tests in a field called `errors`, and a test suite with
        failures in it is the tool working. Only an `error` key means the call did not run."""
        assert not tooling.call_failed({"ok": True, "result": {"errors": ["test_x failed"]}})

    def test_a_string_result_is_not(self):
        assert not tooling.call_failed({"ok": True, "result": "done"})

    def test_nonsense_is_not_counted_either_way(self):
        assert not tooling.call_failed(None)
        assert not tooling.call_failed("whatever")


class TestItRidesHomeOnTheRow:
    def test_the_failures_are_stored_and_come_back(self, tmp_path: Path):
        path = _db(tmp_path)
        repo.messages.add_turn_log(
            path, "2026-09-20T00:00:00+00:00", "chat", "a question",
            ["shell", "edit_files", "edit_files"], 900, 40, 1.0, "answered",
            failed=["edit_files"],
        )
        assert repo.messages.list_turn_log(path)[0]["failed"] == ["edit_files"]

    def test_a_turn_where_nothing_failed_says_so(self, tmp_path: Path):
        path = _db(tmp_path)
        repo.messages.add_turn_log(
            path, "2026-09-20T00:00:00+00:00", "chat", None, ["shell"], 0, 0, 0.0, "answered"
        )
        assert repo.messages.list_turn_log(path)[0]["failed"] == []

    def test_a_row_from_before_the_column_still_reads(self, tmp_path: Path):
        path = tmp_path / "agent.db"
        conn = connect(path)
        try:
            apply_migrations(conn, _migrations()[:48])
            conn.execute(
                "INSERT INTO turn_log (at, mode, focus, tools, tokens_in, tokens_out, seconds, outcome)"
                " VALUES ('2026-01-01T00:00:00','chat','x','[\"shell\"]',9,4,1.0,'answered')"
            )
            conn.commit()
            apply_migrations(conn, _migrations())
        finally:
            conn.close()
        assert repo.messages.list_turn_log(path)[0]["failed"] == []


class TestTheQuestionBecomesAQuery:
    """The whole point: "which of my tools are broken" in one call, not 120 files by hand."""

    def test_it_counts_calls_and_failures_per_tool(self, tmp_path: Path):
        path = _db(tmp_path)
        for _ in range(3):
            repo.messages.add_turn_log(
                path, "2026-09-20T00:00:00+00:00", "chat", None,
                ["shell", "edit_files"], 0, 0, 0.0, "ok", failed=["edit_files"],
            )
        health = {row["tool"]: row for row in repo.messages.tool_health(path)}
        assert health["edit_files"]["calls"] == 3
        assert health["edit_files"]["failed"] == 3
        assert health["shell"]["failed"] == 0

    def test_the_worst_offender_comes_first(self, tmp_path: Path):
        """Sorted by how much is going wrong, because that is the question being asked."""
        path = _db(tmp_path)
        repo.messages.add_turn_log(
            path, "2026-09-20T00:00:00+00:00", "chat", None,
            ["shell"] * 9 + ["edit_files"], 0, 0, 0.0, "ok", failed=["edit_files", "shell"],
        )
        assert repo.messages.tool_health(path)[0]["tool"] == "edit_files"


# --------------------------------------------------------------------------- #
# And a real turn puts it there.
# --------------------------------------------------------------------------- #

import pytest  # noqa: E402

from kith.api.routes import chat as chat_route  # noqa: E402


@pytest.fixture
def feed(db, monkeypatch):
    published: list[dict] = []
    monkeypatch.setattr(chat_route, "AGENT_DB_PATH", db)

    class FakeFeed:
        def publish(self, kind, text, **fields):
            published.append({"kind": kind, "text": text, **fields})

        def charge_session(self, conversation_id, uncached_in, cost_usd):
            return False

    monkeypatch.setattr(chat_route, "feed", FakeFeed())
    return published


class TestATurnRecordsWhichCallsFailed:
    def test_a_failing_call_is_written_down_beside_the_successful_one(self, db, feed):
        turn = chat_route._MindFeed("c1", "change the file")
        turn.saw({"type": "tool_call", "name": "read_file", "arguments": {"paths": "a.py"}})
        turn.saw({"type": "tool_result", "name": "read_file", "result": {"ok": True, "result": "text"}})
        turn.saw({"type": "tool_call", "name": "edit_files", "arguments": {"edits": []}})
        turn.saw(
            {
                "type": "tool_result",
                "name": "edit_files",
                # The shape that hid for weeks: a failure inside a successful envelope.
                "result": {"ok": True, "result": {"error": "edit 1 has no path"}},
            }
        )
        turn.finish()

        row = repo.messages.list_turn_log(db)[0]
        assert row["tools"] == ["read_file", "edit_files"]
        assert row["failed"] == ["edit_files"]

    def test_a_clean_turn_records_no_failures(self, db, feed):
        turn = chat_route._MindFeed("c1", "read it")
        turn.saw({"type": "tool_call", "name": "read_file", "arguments": {}})
        turn.saw({"type": "tool_result", "name": "read_file", "result": {"ok": True, "result": "x"}})
        turn.finish()
        assert repo.messages.list_turn_log(db)[0]["failed"] == []
