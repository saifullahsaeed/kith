"""The flight recorder says how big a turn was. It has never said what was *in* it.

`turn_log` carries `tokens_in`, `tokens_out`, `tokens_uncached` and the tool names — four
numbers about size and one list about activity, and nothing at all about composition. So the
question this codebase keeps having to answer by hand ("what is actually filling the window,
and did that change when we shrank the memory block") has no history to answer it from. The
ledger already computes exactly that breakdown, every round, and `chat.py` already holds the
first and last readings of every turn — it just drops them on the floor when the turn ends.

This is the measurement the context work is judged by, so it lands before the context work.
"""

from __future__ import annotations

from pathlib import Path

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


#: One turn's worth of readings, in the shape `ledger.as_wire()` produces and `chat.py` holds.
BASELINE = [
    {"key": "system", "label": "System prompt", "tokens": 7120, "share": 0.007},
    {"key": "tools", "label": "Tool schemas", "tokens": 12804, "share": 0.012},
    {"key": "present", "label": "Where he is right now", "tokens": 3340, "share": 0.003},
]
FINAL = [*BASELINE, {"key": "code", "label": "Code he has read", "tokens": 260000, "share": 0.25}]


class TestACompositionRidesHomeWithTheCost:
    def test_the_breakdown_is_stored_and_comes_back(self, tmp_path: Path):
        path = _db(tmp_path)
        repo.messages.add_turn_log(
            path,
            "2026-09-19T00:00:00+00:00",
            "chat",
            "a question",
            ["shell"],
            900,
            40,
            1.0,
            "answered",
            tokens_uncached=100,
            context={"baseline": BASELINE, "final": FINAL},
        )
        row = repo.messages.list_turn_log(path)[0]
        assert row["context"] == {"baseline": BASELINE, "final": FINAL}

    def test_a_turn_that_recorded_no_reading_says_so_rather_than_inventing_one(self, tmp_path: Path):
        """A local model reports no window, so the ledger has nothing to say. That is a real
        state and it must read as absent — an empty breakdown would be indistinguishable from
        a turn that genuinely carried nothing, which is never true."""
        path = _db(tmp_path)
        repo.messages.add_turn_log(path, "2026-09-19T00:00:00+00:00", "chat", None, [], 0, 0, 0.0, "answered")
        assert repo.messages.list_turn_log(path)[0]["context"] is None

    def test_a_malformed_row_is_not_worth_a_crash(self, tmp_path: Path):
        """Same rule `_tools` already follows: the flight recorder is read by a dashboard, and
        one unreadable row must not take the screen down with it."""
        path = _db(tmp_path)
        repo.messages.add_turn_log(path, "2026-09-19T00:00:00+00:00", "chat", None, [], 0, 0, 0.0, "answered")
        conn = connect(path)
        try:
            conn.execute("UPDATE turn_log SET context = 'not json'")
            conn.commit()
        finally:
            conn.close()
        assert repo.messages.list_turn_log(path)[0]["context"] is None


class TestTheColumnArrivesWithoutLosingARow:
    def test_the_migration_can_be_run_twice(self, tmp_path: Path):
        """An install receives a migration by having its `user_version` wound back and `init`
        run again — `test_the_doing_layer_knows_its_project` does exactly that, and v47 carries
        a try/except for the same reason. An additive ALTER that cannot survive being reapplied
        turns a re-run into a hard failure partway through the list, leaving every migration
        behind it unapplied."""
        path = _db(tmp_path)
        conn = connect(path)
        try:
            conn.execute("PRAGMA user_version = 47")
            conn.commit()
        finally:
            conn.close()
        conn = connect(path)
        try:
            apply_migrations(conn, _migrations())  # must not raise
        finally:
            conn.close()

    def test_rows_written_before_the_column_existed_survive(self, tmp_path: Path):
        path = tmp_path / "agent.db"
        conn = connect(path)
        try:
            apply_migrations(conn, _migrations()[:47])  # everything up to v47
            conn.execute(
                "INSERT INTO turn_log (at, mode, focus, tools, tokens_in, tokens_out, seconds, outcome)"
                " VALUES ('2026-01-01T00:00:00', 'chat', 'a task', '[]', 900, 40, 1.0, 'answered')"
            )
            conn.commit()
            apply_migrations(conn, _migrations())
            rows = conn.execute("SELECT tokens_in, outcome, context FROM turn_log").fetchall()
        finally:
            conn.close()
        assert [tuple(r) for r in rows] == [(900, "answered", None)]


# --------------------------------------------------------------------------- #
# And the turn actually puts one there.
# --------------------------------------------------------------------------- #

import pytest  # noqa: E402

from kith.api.routes import chat as chat_route  # noqa: E402


@pytest.fixture
def feed(db, monkeypatch):
    """A `_MindFeed` writing to a temp database. Same harness as
    `test_a_chat_turn_is_on_the_record`, and patched the same way for the same reason:
    the package re-exports the feed singleton, so only `sys.modules` patching takes."""
    published: list[dict] = []
    monkeypatch.setattr(chat_route, "AGENT_DB_PATH", db)

    class FakeFeed:
        def publish(self, kind, text, **fields):
            published.append({"kind": kind, "text": text, **fields})

        def charge_session(self, conversation_id, uncached_in, cost_usd):
            return False

    monkeypatch.setattr(chat_route, "feed", FakeFeed())
    return published


def _reading(lines):
    return {"window": 1_048_576, "used": sum(x["tokens"] for x in lines), "lines": lines}


class TestTheTurnWritesItsOwnComposition:
    def test_the_first_and_last_readings_both_ride_home(self, db, feed):
        """Both, because they answer different questions. The first is what arrived *before*
        the turn did any work — persona, tools, injected memory — which is the number the
        context-injection work moves. The last is what the turn grew to, which is the number
        the trimming work moves. One without the other cannot tell those apart."""
        watcher = chat_route._MindFeed("c1", "a question")
        watcher.saw({"type": "context", "context": _reading(BASELINE)})
        watcher.saw({"type": "context", "context": _reading(FINAL)})
        watcher.finish()

        stored = repo.messages.list_turn_log(db)[0]["context"]
        assert stored["baseline"] == BASELINE
        assert stored["final"] == FINAL

    def test_a_turn_that_never_reported_a_reading_stores_nothing(self, db, feed):
        chat_route._MindFeed("c1", "a question").finish()
        assert repo.messages.list_turn_log(db)[0]["context"] is None
