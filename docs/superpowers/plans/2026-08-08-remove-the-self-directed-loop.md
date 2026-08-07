# Remove the Self-Directed Loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete autonomy — Kith choosing his own work and running it unattended — while keeping the scheduler and activity feed that were bundled into the same class, and keeping cost tracking under a name that is true.

**Architecture:** `kith/autonomy/runner.py` is one 1,291-line class doing three jobs. Two of them get extracted into `kith/services/activity.py` (the Mind feed and session cost ledger, which chat already publishes through) and `kith/services/scheduler.py` (due reminders and schedules waking their own chat). The third — the round-robin loop that picks work — is deleted along with the rest of `kith/autonomy/`. The `tick_log` table becomes `turn_log`; it was never tick-specific, it is where chat turns record what they cost.

**Tech Stack:** Python 3.13, Flask + APIFlask, SQLAlchemy 2.x, raw-SQL migrations, pytest. React 19 + TypeScript + Vite on the front end.

## Global Constraints

- Run every check with `./check` from `kith/`. Never `pytest | tail` — a pipeline reports the exit status of `tail`. This rule exists because a red suite once scrolled past as one line of green.
- `ruff` line length is 110. `./check` runs `ruff check`, `ruff format --check`, `pytest`, `tsc -b --force`, `vite build`.
- Server tests run from `kith/server` as `.venv/bin/python -m pytest`.
- Persona is untouched. No file under `server/persona/` is edited, and neither is `kith/services/persona.py` or `kith/api/routes/persona.py`.
- Projects, tasks, milestones and the roadmap all stay, including every task status.
- SQLite is 3.50.4, so `ALTER TABLE ... DROP COLUMN` and `... RENAME TO` are both available.
- Migrations are appended to the list returned by `_migrations()` in `kith/infra/db/migrations.py` and never renumbered. The next free number is **v29**.
- Do not delete a test file without first checking it against Task 9's triage table. One file that reads as tick-specific must be kept.

---

## File Structure

**Created**

| File | Responsibility |
|---|---|
| `server/kith/services/activity.py` | The live Mind feed (`publish`/`subscribe`/`unsubscribe`/`recent`), session cost accounting, and `status()`. No threads, no decisions. |
| `server/kith/services/scheduler.py` | One timer thread. Asks "is anything due?" and wakes that conversation. Knows nothing about tasks. |
| `server/kith/api/routes/activity.py` | `GET /activity`, `GET /activity/stream`, `GET /usage`. |
| `server/tests/test_the_feed_survives_its_loop.py` | The feed and cost ledger still work with no loop in the tree. |
| `server/tests/test_a_due_reminder_still_wakes_its_chat.py` | Reminders and schedules fire from the scheduler. |
| `server/tests/test_the_cost_log_survives_its_rename.py` | v29 preserves every row. |

**Deleted**

`server/kith/autonomy/` entirely (`runner.py`, `prompts.py`, `toolsets.py`, `directives.py`, `directives/*.md`, `__init__.py`), `server/kith/api/routes/autonomy.py`, `ui/src/hooks/use-autonomy.ts`, `ui/src/lib/backend/autonomy.ts`.

**Modified**

`server/kith/api/routes/chat.py` (feed import, `add_turn_log`, vocabulary), `server/kith/api/routes/__init__.py` (route registration), `server/kith/infra/db/migrations.py` (v29), `server/kith/infra/db/models.py` (`TurnLog`, drop `Conversation.working`), `server/kith/infra/db/repositories/messages.py` (renames), `server/kith/infra/db/repositories/conversations.py` (drop working helpers), `server/kith/domain/tuning.py` (drop loop-only knobs), `server/app.py` if it references the runner, plus the UI files listed in Task 10.

---

### Task 1: Extract the activity feed and cost ledger

The feed must come out first: chat publishes through it on every turn, so nothing else can be deleted until it has a home that does not depend on the loop.

**Files:**
- Create: `server/kith/services/activity.py`
- Create: `server/tests/test_the_feed_survives_its_loop.py`
- Modify: `server/kith/api/routes/chat.py:520-527`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: a module-level singleton `feed` in `kith.services.activity` with
  `publish(kind: str, text: str, *, tokens: dict | None = None, tool: str | None = None, args: dict | None = None, conversation: str = "") -> None`,
  `subscribe() -> queue.Queue`, `unsubscribe(q: queue.Queue) -> None`, `recent() -> list[dict]`,
  `charge_session(conversation_id: str, uncached_in: int, cost_usd: float) -> bool` (True when the session has passed its budget),
  and `status() -> dict` with keys `working: list[str]`, `tokensIn`, `tokensOut`, `tokensUncached`, `costUsd`.

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_the_feed_survives_its_loop.py
"""The Mind feed outlives the loop it was written inside.

`_MindFeed` in the chat route publishes every chat turn through what used to be
`runner.publish`. The runner also happened to contain the loop that picked work on its own.
Deleting that loop must not take the feed down with it — a conversation is work too, and it
was once the one kind that left no trace here.
"""

from __future__ import annotations

from kith.services import activity


class TestPublishingWithoutALoop:
    def test_a_line_reaches_a_subscriber(self):
        q = activity.feed.subscribe()
        try:
            activity.feed.publish("tool", "read a file", conversation="c1")
            line = q.get(timeout=1)
        finally:
            activity.feed.unsubscribe(q)
        assert line["kind"] == "tool"
        assert line["text"] == "read a file"
        assert line["conversation"] == "c1"

    def test_recent_keeps_what_was_published(self):
        activity.feed.publish("reply", "you: hello", conversation="c2")
        assert any(item["text"] == "you: hello" for item in activity.feed.recent())

    def test_an_unsubscribed_reader_stops_receiving(self):
        q = activity.feed.subscribe()
        activity.feed.unsubscribe(q)
        activity.feed.publish("tool", "after", conversation="c3")
        assert q.qsize() == 0
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd server && .venv/bin/python -m pytest tests/test_the_feed_survives_its_loop.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'kith.services.activity'`

- [ ] **Step 3: Create the module**

Move — do not rewrite — `_emit`, `subscribe`, `unsubscribe`, `recent`, `publish`, `_charge_session`, and the state they use (`_subscribers`, `_buffer`, `_state_lock`, `_session_cost`, `_session_tokens`, `_session_capped`, `_tokens_in`, `_tokens_out`, `_tokens_uncached`, `_cost_usd`) out of `AutonomyRunner` into a `Feed` class in `server/kith/services/activity.py`, then `feed = Feed()` at module scope. Keep every docstring verbatim; they carry measurements worth keeping.

Two deliberate changes while moving:

1. `publish` takes its extras as keyword-only arguments rather than `**fields`, so a typo is an error rather than a silently dropped field.
2. `charge_session` **returns a bool instead of resting the session.** It used to call `repo.conversations.set_working(..., False)`, and "rest the session" is a roaming idea that will not exist. It now returns `True` when the budget is passed and leaves acting on that to the caller. Keep the `repo.messages.add_message(...)` line that tells the person why — that is the whole point of the cap — and delete only the `set_working` call.

- [ ] **Step 4: Run the test and watch it pass**

Run: `cd server && .venv/bin/python -m pytest tests/test_the_feed_survives_its_loop.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Point chat at the new feed**

In `server/kith/api/routes/chat.py`, replace the body of `_MindFeed._publish`:

```python
    def _publish(self, kind: str, text: str, **fields) -> None:
        try:
            from kith.services.activity import feed

            feed.publish(kind, text, conversation=self.conversation_id, **fields)
        except Exception:
            # A feed line must never be the thing that takes a turn down.
            pass
```

The local import stays: it is what lets a test swap the feed without importing the world.

- [ ] **Step 6: Run the chat-side tests**

Run: `cd server && .venv/bin/python -m pytest tests/test_a_chat_turn_is_on_the_record.py tests/test_mind_feed.py tests/test_stopping_a_turn_stops_that_turn.py -q`
Expected: PASS. These patch `sys.modules["kith.autonomy.runner"]`; update those patches to `kith.services.activity` in this step, since they are asserting the seam you just moved.

- [ ] **Step 7: Commit**

```bash
git add server/kith/services/activity.py server/tests/test_the_feed_survives_its_loop.py \
        server/kith/api/routes/chat.py server/tests/test_a_chat_turn_is_on_the_record.py \
        server/tests/test_mind_feed.py server/tests/test_stopping_a_turn_stops_that_turn.py
git commit -m "Give the Mind feed a home that does not depend on the loop"
```

---

### Task 2: Extract the scheduler

**Files:**
- Create: `server/kith/services/scheduler.py`
- Create: `server/tests/test_a_due_reminder_still_wakes_its_chat.py`

**Interfaces:**
- Consumes: `kith.services.activity.feed` from Task 1.
- Produces: `kith.services.scheduler` exposing `fire_due(now_iso: str) -> list[str]` (the conversation ids woken, ordered) and `start() -> None` / `stop() -> None` for the timer thread.

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_a_due_reminder_still_wakes_its_chat.py
"""A reminder wakes its own conversation, with nothing looping.

`_fire_conversation_reminders` lived inside `_tick` for one reason: `_tick` was the only
thing running. It never chose work and never read a task. Pulling it out is what lets the
loop go without taking "remind me at 3pm" with it.
"""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo
from kith.services import conversations, scheduler


class TestWhatIsDueWakesItsOwnChat:
    def test_a_due_reminder_continues_that_conversation(self, db: Path, monkeypatch):
        monkeypatch.setattr(scheduler, "AGENT_DB_PATH", db)
        woken: list[str] = []
        monkeypatch.setattr(scheduler, "_continue", lambda cid, notes: woken.append(cid))
        conv = conversations.start(db, "hi")["id"]
        repo.reminders.add_reminder(db, "2020-01-01T00:00:00+00:00", "check it", conversation_id=conv)

        scheduler.fire_due("2030-01-01T00:00:00+00:00")

        assert woken == [conv]

    def test_a_reminder_with_no_conversation_is_left_alone(self, db: Path, monkeypatch):
        """It belongs to nobody's chat, so there is no chat to continue."""
        monkeypatch.setattr(scheduler, "AGENT_DB_PATH", db)
        woken: list[str] = []
        monkeypatch.setattr(scheduler, "_continue", lambda cid, notes: woken.append(cid))
        repo.reminders.add_reminder(db, "2020-01-01T00:00:00+00:00", "no chat")

        scheduler.fire_due("2030-01-01T00:00:00+00:00")

        assert woken == []

    def test_two_reminders_for_one_chat_wake_it_once(self, db: Path, monkeypatch):
        monkeypatch.setattr(scheduler, "AGENT_DB_PATH", db)
        seen: list[tuple[str, int]] = []
        monkeypatch.setattr(scheduler, "_continue", lambda cid, notes: seen.append((cid, len(notes))))
        conv = conversations.start(db, "hi")["id"]
        repo.reminders.add_reminder(db, "2020-01-01T00:00:00+00:00", "first", conversation_id=conv)
        repo.reminders.add_reminder(db, "2020-01-01T00:00:00+00:00", "second", conversation_id=conv)

        scheduler.fire_due("2030-01-01T00:00:00+00:00")

        assert seen == [(conv, 2)]

    def test_nothing_due_wakes_nobody(self, db: Path, monkeypatch):
        monkeypatch.setattr(scheduler, "AGENT_DB_PATH", db)
        woken: list[str] = []
        monkeypatch.setattr(scheduler, "_continue", lambda cid, notes: woken.append(cid))
        conversations.start(db, "hi")

        scheduler.fire_due("2030-01-01T00:00:00+00:00")

        assert woken == []
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd server && .venv/bin/python -m pytest tests/test_a_due_reminder_still_wakes_its_chat.py -q`
Expected: FAIL — `ImportError: cannot import name 'scheduler'`

- [ ] **Step 3: Create the module**

Move `_fire_conversation_reminders` and `_continue_conversation` from `AutonomyRunner` into `server/kith/services/scheduler.py` as `fire_due(now_iso)` and `_continue(conversation_id, notes)`. Keep the grouping behaviour: two reminders due for one chat become one continuation, told about both — that is what `test_two_reminders_for_one_chat_wake_it_once` pins.

`_continue` calls the same `_turn` the chat route calls:

```python
def _continue(conversation_id: str, notes: list[str]) -> None:
    from kith.api.routes.chat import _Recorder, _build_messages, _turn

    config = default_config()
    trigger = (
        "Some reminders just fired for this conversation. Pick them up where you left off, "
        "briefly, the way you would mid-conversation, not a report — or that nothing "
        "has, if that's the honest answer.\n\n" + "\n".join(f"- {note}" for note in notes)
    )
    history = [*conversations.full_messages(conversation_id), {"role": "user", "content": trigger}]
    messages = _build_messages(history, config, conversation_id)
    conversations.record(AGENT_DB_PATH, conversation_id, "user", trigger)
    recorder = _Recorder(conversation_id)
    with session_context.working_in(conversation_id):
        for _ in _turn(recorder, messages, config, conversation_id, opening=trigger):
            pass
```

`start()` runs a daemon thread that calls `fire_due(clock.now_iso())` every 30 seconds; `stop()` sets an Event it checks. It does **not** start on import — see Task 9.

- [ ] **Step 4: Run the test and watch it pass**

Run: `cd server && .venv/bin/python -m pytest tests/test_a_due_reminder_still_wakes_its_chat.py -q`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add server/kith/services/scheduler.py server/tests/test_a_due_reminder_still_wakes_its_chat.py
git commit -m "Reminders and schedules get a scheduler instead of a loop"
```

---

### Task 3: Rename tick_log to turn_log

Done before the deletion, so cost tracking is proven intact while the old code is still there to compare against.

**Files:**
- Modify: `server/kith/infra/db/migrations.py`
- Modify: `server/kith/infra/db/models.py:297-300`
- Modify: `server/kith/infra/db/repositories/messages.py`
- Create: `server/tests/test_the_cost_log_survives_its_rename.py`

**Interfaces:**
- Produces: `repo.messages.add_turn_log(...)`, `repo.messages.list_turn_log(path, limit)`, `repo.messages.turn_log_summary(path)` — same signatures as the `tick_log` versions they replace.

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_the_cost_log_survives_its_rename.py
"""The flight recorder keeps its rows through the rename.

`tick_log` was never tick-specific — `add_tick_log(..., mode="chat", ...)` is called from
`_MindFeed.finish()`, so this table is where a conversation records what it cost. The name
goes with the loop; losing a single row would be the worst available outcome of that.
"""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo
from kith.infra.db.connection import connect
from kith.infra.db.migrations import apply_migrations, _migrations


class TestTheRenameCarriesEveryRow:
    def test_rows_written_before_the_rename_are_still_there(self, tmp_path: Path):
        path = tmp_path / "agent.db"
        conn = connect(path)
        try:
            apply_migrations(conn, _migrations()[:28])  # everything up to and including v28
            conn.execute(
                "INSERT INTO tick_log (at, mode, focus, tools, tokens_in, tokens_out, seconds, outcome)"
                " VALUES ('2026-01-01T00:00:00', 'chat', 'a task', '[]', 900, 40, 1.0, 'answered')"
            )
            conn.commit()
            apply_migrations(conn, _migrations())  # now v29
            rows = conn.execute("SELECT mode, tokens_in, tokens_out FROM turn_log").fetchall()
        finally:
            conn.close()
        assert [tuple(r) for r in rows] == [("chat", 900, 40)]

    def test_the_old_table_is_gone(self, tmp_path: Path):
        path = tmp_path / "agent.db"
        conn = connect(path)
        try:
            apply_migrations(conn, _migrations())
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        assert "turn_log" in names
        assert "tick_log" not in names

    def test_the_working_column_is_gone(self, tmp_path: Path):
        """`working` meant "this session roams", which is the idea being removed."""
        path = tmp_path / "agent.db"
        conn = connect(path)
        try:
            apply_migrations(conn, _migrations())
            cols = {r[1] for r in conn.execute("PRAGMA table_info(conversations)")}
        finally:
            conn.close()
        assert "working" not in cols
        assert "project_id" in cols  # the other half of v25 stays


class TestTheRepositoryStillRecords:
    def test_a_row_goes_in_and_comes_back(self, db: Path):
        repo.messages.add_turn_log(
            db, "2026-01-01T00:00:00", "chat", "a task", ["read_file"], 900, 40, 1.0, "answered",
            tokens_uncached=900,
        )
        rows = repo.messages.list_turn_log(db, 10)
        assert len(rows) == 1
        assert (rows[0]["mode"], rows[0]["tokens_out"]) == ("chat", 40)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd server && .venv/bin/python -m pytest tests/test_the_cost_log_survives_its_rename.py -q`
Expected: FAIL — no `turn_log` table, no `add_turn_log`.

- [ ] **Step 3: Add migration v29**

In `server/kith/infra/db/migrations.py`, above the `return [...]`:

```python
    def v29_turn_log(conn):
        # `tick_log` was never about ticks. `add_tick_log(..., mode="chat", ...)` is called
        # from the chat route, so this table is the flight recorder for *turns* — a
        # conversation's cost lives here and is most of what the money dashboard reads. The
        # loop it was named after is gone; the rows are not.
        #
        # `conversations.working` goes in the same step. It meant "this session keeps going
        # without being asked", which is the idea being removed rather than renamed. Its
        # sibling from v25, `project_id`, stays: what a session is working on still matters.
        conn.execute("ALTER TABLE tick_log RENAME TO turn_log")
        conn.execute("ALTER TABLE conversations DROP COLUMN working")
```

Append `v29_turn_log` to the returned list.

- [ ] **Step 4: Rename the model and the repository functions**

In `models.py`, `class TickLog` → `class TurnLog`, `__tablename__ = "turn_log"`, and its docstring becomes "One row per turn — the durable flight recorder." Remove `working` from the `Conversation` model.

In `repositories/messages.py`, rename `add_tick_log` → `add_turn_log`, `list_tick_log` → `list_turn_log`, `tick_log_summary` → `turn_log_summary`, leaving every signature and body otherwise alone.

In `repositories/conversations.py`, delete `set_working`, `is_working`, `working_sessions`.

- [ ] **Step 5: Update the callers**

```bash
cd server && grep -rn "add_tick_log\|list_tick_log\|tick_log_summary\|set_working\|is_working\|working_sessions" kith/ tests/
```

Fix each hit. `kith/api/routes/chat.py:567` is `add_tick_log` → `add_turn_log`.

- [ ] **Step 6: Run the tests**

Run: `cd server && .venv/bin/python -m pytest tests/test_the_cost_log_survives_its_rename.py tests/test_what_it_cost.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add server/kith/infra/db server/kith/api/routes/chat.py server/tests/test_the_cost_log_survives_its_rename.py
git commit -m "tick_log becomes turn_log, because that is what it always recorded"
```

---

### Task 4: Rehome the activity and usage routes

**Files:**
- Create: `server/kith/api/routes/activity.py`
- Modify: `server/kith/api/routes/__init__.py`
- Delete: `server/kith/api/routes/autonomy.py`

**Interfaces:**
- Consumes: `kith.services.activity.feed` from Task 1.
- Produces: `GET /activity`, `GET /activity/stream`, `GET /usage`.

- [ ] **Step 1: Write the failing test**

Add to `server/tests/test_the_feed_survives_its_loop.py`:

```python
class TestTheRoutes:
    def test_activity_returns_recent_lines(self, client):
        activity.feed.publish("tool", "listed files", conversation="c9")
        body = client.get("/api/activity").get_json()
        assert any(item["text"] == "listed files" for item in body["activity"])

    def test_usage_reports_a_total(self, client):
        body = client.get("/api/usage").get_json()
        assert "costUsd" in body

    def test_the_autonomy_routes_are_gone(self, client):
        assert client.get("/api/autonomy").status_code == 404
        assert client.post("/api/autonomy", json={}).status_code == 404
```

Use whatever `client` fixture the existing route tests use — check `tests/test_api_auth.py` for the pattern and reuse it rather than inventing one.

- [ ] **Step 2: Run it and watch it fail**

Run: `cd server && .venv/bin/python -m pytest tests/test_the_feed_survives_its_loop.py -q`
Expected: FAIL — `/api/autonomy` still answers 200.

- [ ] **Step 3: Create `routes/activity.py`**

Copy `GET /activity`, `GET /usage` and the SSE handler from `routes/autonomy.py` verbatim, changing only `from kith.autonomy import runner as autonomy` to `from kith.services.activity import feed` and the calls that follow. `GET /autonomy/stream` becomes `GET /activity/stream`. Drop `GET /autonomy` and `POST /autonomy` entirely.

- [ ] **Step 4: Register it and delete the old module**

In `routes/__init__.py`, replace `autonomy,` with `activity,` in the import list. Then `git rm server/kith/api/routes/autonomy.py`.

- [ ] **Step 5: Run the tests**

Run: `cd server && .venv/bin/python -m pytest tests/test_the_feed_survives_its_loop.py tests/test_api_auth.py -q`
Expected: PASS. `test_api_auth.py` enumerates routes; update its expected list in this step.

- [ ] **Step 6: Commit**

```bash
git add -A server/kith/api/routes server/tests
git commit -m "The feed gets its own routes; /autonomy goes"
```

---

### Task 5: Delete the loop

**Files:**
- Delete: `server/kith/autonomy/` (whole directory)
- Modify: every module that imported from it

- [ ] **Step 1: Find every importer**

```bash
cd server && grep -rn "kith.autonomy\|from kith import autonomy" kith/ tests/ ../desktop/src ../ui/src
```

Write the list down before deleting anything — the compiler will not help you here.

- [ ] **Step 2: Delete the package**

```bash
cd server && git rm -r kith/autonomy
```

- [ ] **Step 3: Fix the importers**

`kith/services/agent_loop.py` imports the toolsets; the chat path uses `only=` narrowing that lives in `agent_loop` itself, so remove the autonomy toolset import and any branch reachable only from a tick. Do not change what chat is offered.

- [ ] **Step 4: Run the whole suite and read the failures**

Run: `cd server && .venv/bin/python -m pytest tests/ -q 2>&1 | tail -40`
Expected: a large number of collection errors from tests importing `kith.autonomy`. That is the input to Task 9 — do not fix them here, and do not delete them yet.

- [ ] **Step 5: Commit**

```bash
git add -A server/kith
git commit -m "Delete the self-directed loop"
```

---

### Task 6: Drop the loop-only tuning knobs

**Files:**
- Modify: `server/kith/domain/tuning.py`
- Modify: `server/tests/test_tuning.py`

- [ ] **Step 1: Remove the knobs**

Delete the `Tunable(...)` entries for `focus_grind_limit`, `stall_break` and `stall_giveup`. Keep `max_rounds`, `landing_reserve`, `landing_effort`, `session_cost_cents`, `session_token_cap` — all are read by chat.

- [ ] **Step 2: Run the tuning tests**

Run: `cd server && .venv/bin/python -m pytest tests/test_tuning.py -q`
Expected: failures where the test names a removed knob. `test_tuning.py` parametrises across the whole `TUNABLES` tuple, so most tests adapt on their own; fix the ones that name a knob explicitly.

- [ ] **Step 3: Commit**

```bash
git add server/kith/domain/tuning.py server/tests/test_tuning.py
git commit -m "Drop the knobs that only ever steered the loop"
```

---

### Task 7: Session budget stops a turn instead of resting a session

`charge_session` returns a bool now (Task 1). Nothing acts on it yet.

**Files:**
- Modify: `server/kith/api/routes/chat.py`
- Modify: `server/tests/test_a_session_stops_at_its_budget.py`

- [ ] **Step 1: Write the failing test**

```python
def test_passing_the_budget_stops_the_turn(self, db, conversation, feed, monkeypatch):
    """The cap used to 'rest' the session, which was a roaming idea. With roaming gone the
    only thing left to stop is the turn — which is exactly what the stop switch is for."""
    monkeypatch.setattr(activity.feed, "charge_session", lambda *a, **k: True)
    events = [
        {"type": "stats", "stats": {"promptTokens": 9, "responseTokens": 9, "uncachedTokens": 9}},
        {"type": "delta", "role": "text", "text": "should not arrive"},
    ]
    seen = _drive(monkeypatch, conversation, events, stop_after=None)
    assert not [line for line in seen if line.get("text") == "should not arrive"]
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd server && .venv/bin/python -m pytest tests/test_a_session_stops_at_its_budget.py -q`
Expected: FAIL — the turn runs on.

- [ ] **Step 3: Wire it up**

In `_MindFeed.saw`'s `stats` branch, call `feed.charge_session(...)` and, when it returns True, set the turn's stopping Event — the same switch `_stop()` uses. The person still gets the message `charge_session` writes.

- [ ] **Step 4: Run the test**

Run: `cd server && .venv/bin/python -m pytest tests/test_a_session_stops_at_its_budget.py tests/test_the_budget_is_money.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/kith/api/routes/chat.py server/tests/test_a_session_stops_at_its_budget.py
git commit -m "A session past its budget stops its turn"
```

---

### Task 8: Start the scheduler where the loop used to start

**Files:**
- Modify: `server/app.py`
- Modify: `server/tests/test_the_suite_does_not_start_loops.py`

- [ ] **Step 1: Repoint the guard test**

`test_the_suite_does_not_start_loops.py` guards a hazard the scheduler inherits: a background thread that outlives a test and reads the real `server/data/agent.db`. Rewrite its assertions against `scheduler.start` rather than `ensure_loop`, keeping the docstring's account of why teardown is the wrong end of the problem.

- [ ] **Step 2: Run it and watch it fail**

Run: `cd server && .venv/bin/python -m pytest tests/test_the_suite_does_not_start_loops.py -q`
Expected: FAIL — it still names `ensure_loop`.

- [ ] **Step 3: Start the scheduler from `app.py`, and neuter it in tests**

Call `scheduler.start()` where the app boots, never at import. In `tests/conftest.py`, the autouse fixture that stubbed `AutonomyRunner.ensure_loop` becomes one that stubs `scheduler.start`.

- [ ] **Step 4: Run the test**

Run: `cd server && .venv/bin/python -m pytest tests/test_the_suite_does_not_start_loops.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/app.py server/tests/conftest.py server/tests/test_the_suite_does_not_start_loops.py
git commit -m "Boot the scheduler, and keep the suite from starting one"
```

---

### Task 9: Triage the tests

49 of 102 test files reference ticks. Work the table; do not improvise.

**Delete outright** — every test in the file asserts behaviour that no longer exists:

`test_stopping_a_step.py`, `test_where_a_tick_works.py`, `test_getting_on_with_it.py`,
`test_stall_detection.py`, `test_idle_reasons.py`, `test_a_task_that_never_progresses_escalates.py`,
`test_a_session_that_keeps_working.py`, `test_a_tick_gets_room_to_finish.py`,
`test_work_directive_asks_for_attempt_shape.py`, `test_a_tick_can_read_its_own_notes.py`,
`test_which_upstream_a_step_lands_on.py`, `test_a_stall_is_not_a_missing_tool.py`.

**Rewrite against a chat turn** — the behaviour survives, only its trigger was a tick:

`test_a_reminder_reports_back_to_its_chat.py` (drive `scheduler.fire_due`),
`test_what_it_cost.py`, `test_the_budget_is_money.py`, `test_a_provider_error_backs_off.py`,
`test_the_last_round_costs_what_the_others_did.py`, `test_a_session_owns_its_project.py`,
`test_a_session_stays_on_its_project.py`, `test_roadmap.py`, `test_api_auth.py`,
`test_a_closed_project_hides_its_work.py`, `test_a_plan_waits_for_a_look.py`,
`test_finished_work_waits_for_a_look.py`, `test_a_project_with_no_tasks.py`, `test_paths.py`.

**Keep, repointed** — Task 8 already did this one:

`test_the_suite_does_not_start_loops.py`.

- [ ] **Step 1: Delete the first group**

```bash
cd server && git rm tests/test_stopping_a_step.py tests/test_where_a_tick_works.py \
  tests/test_getting_on_with_it.py tests/test_stall_detection.py tests/test_idle_reasons.py \
  tests/test_a_task_that_never_progresses_escalates.py tests/test_a_session_that_keeps_working.py \
  tests/test_a_tick_gets_room_to_finish.py tests/test_work_directive_asks_for_attempt_shape.py \
  tests/test_a_tick_can_read_its_own_notes.py tests/test_which_upstream_a_step_lands_on.py \
  tests/test_a_stall_is_not_a_missing_tool.py
```

Before each deletion, open the file and check no test in it asserts something from the second group. If one does, move that test into the surviving file rather than losing it.

- [ ] **Step 2: Rewrite the second group, one file per commit**

For each: replace the tick driver with a chat turn. Where a test called `runner._step(None, "")`, drive `route._turn(...)` with a stubbed `stream_agent` — `tests/test_stopping_a_turn_stops_that_turn.py::_drive` is the pattern to copy.

- [ ] **Step 3: Run the whole suite**

Run: `cd server && .venv/bin/python -m pytest tests/ -q`
Expected: PASS, no collection errors.

- [ ] **Step 4: Commit**

```bash
git add -A server/tests
git commit -m "Retire the tests for a loop that no longer exists"
```

---

### Task 10: Remove autonomy from the interface

**Files:**
- Delete: `ui/src/hooks/use-autonomy.ts`, `ui/src/lib/backend/autonomy.ts`
- Modify: `ui/src/components/shell/workspace.tsx`, `shell/app-header.tsx`, `shell/header-controls.tsx`, `shell/presence.tsx`, `chat/session-bar.tsx`, `chat/work-panel.tsx`, `lib/backend/index.ts`, `lib/tokens.ts`

- [ ] **Step 1: Replace the hook with an activity hook**

`use-autonomy.ts` becomes `use-activity.ts`, reading `/api/activity/stream` and `/api/activity`. It keeps the `activity` list the Mind panel renders and drops `status.working`, `keepWorking`, `rest`, `nudge`, `tickNow`.

- [ ] **Step 2: Strip the roaming controls**

Remove the roam toggle from `header-controls.tsx` and the keep-going control from `session-bar.tsx`. In `workspace.tsx`, `const working = (autonomy.status?.working ?? []).length > 0` and `sessionWorking` both go; the green wash is driven by the reliability work's live-turn signal later, so for now it is simply off. Leave a comment saying so, naming the follow-on spec.

- [ ] **Step 3: Typecheck**

Run: `cd ui && npx tsc -b --force`
Expected: clean. Fix each unused import it reports rather than suppressing it.

- [ ] **Step 4: Commit**

```bash
git add -A ui/src
git commit -m "Take the autonomy controls out of the interface"
```

---

### Task 11: Scrub the vocabulary and prove it green

- [ ] **Step 1: Find what still speaks of ticks**

```bash
cd /Users/saifullahsaeed/Desktop/personal/ai-fun/kith
grep -rniE "\btick\b|autonom|roam|unattended" server/kith server/tests ui/src desktop/src README.md server/README.md ui/README.md
```

- [ ] **Step 2: Rewrite each comment so the concept reads as never having existed**

Do not simply delete the sentences. Several carry a measurement or a bug story worth keeping — `chat.py`'s note about a 40k tick being visible while a 200k chat turn was not is the reason the cost log exists. Rewrite it in terms of turns.

- [ ] **Step 3: Update the READMEs**

The root `README.md` advertises "turn on autonomy and it takes self-directed steps in the background". That claim goes. `server/README.md` describes the autonomy package.

- [ ] **Step 4: Run the gate**

Run: `cd /Users/saifullahsaeed/Desktop/personal/ai-fun/kith && ./check`
Expected: `all checks passed`

- [ ] **Step 5: Build the desktop app**

Run: `cd desktop && npm run build`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "Scrub the vocabulary of a loop that no longer exists"
```

---

## Self-Review

**Spec coverage.** Runner split → Tasks 1, 2. Autonomy deleted → Task 5. API surface → Task 4. `tick_log` → `turn_log` and `working` dropped → Task 3. Persona untouched → Global Constraints; no task edits it. Projects/tasks kept → no task touches them. Tuning knobs → Task 6. Test triage including the keep-repointed file → Tasks 8, 9. UI → Task 10. Vocabulary → Task 11.

**Gap found and closed.** The spec said `_charge_session` stays, but its body calls `set_working(..., False)` — a roaming action that cannot survive. Task 1 Step 3 changes it to return a bool and Task 7 acts on it by stopping the turn. Without this the budget cap would have been silently removed along with roaming.

**Type consistency.** `feed.publish(kind, text, *, tokens, tool, args, conversation)` in Task 1 matches the call in Task 1 Step 5 and Task 4. `charge_session(...) -> bool` in Task 1 matches Task 7. `scheduler.fire_due(now_iso)` and `_continue(conversation_id, notes)` in Task 2 match the tests that patch them.

**Ordering.** The feed moves before anything is deleted, because chat publishes through it. The rename lands before the deletion, so cost tracking is proven while the old code is still present to compare against.
