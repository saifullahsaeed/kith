# Kith Autonomy Cost & Planning Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop an unattended "keep working" session from burning tens of millions of tokens by re-doing work it can't remember, and make the initial task plan good enough that execution can't grind on a bad one.

**Architecture:** Kith runs a stateless tick loop — each tick is a fresh conversation seeded only from durable memory. The root cause of the 57M-token night is that a tick can't see its own prior attempts, so it re-orients and re-attempts endlessly, with no cumulative ceiling. We fix it in layers: a seatbelt (per-session budget) first so nothing else can run away while we work; then the "baton" (deeper, better-shaped handoff); then leanness (smaller per-round window); then honest stall/escalation; then planning-quality gates at the one task-creation chokepoint; then a "right foot" plan-proposal directive.

**Tech Stack:** Python 3.13, SQLite (real migrations, no ORM mocks in tests), pytest, OpenRouter (OpenAI-compatible) + Ollama transports.

## Global Constraints

- Repo root (git) is `/Users/saifullahsaeed/Desktop/personal/ai-fun/kith`; the Python project is `kith/server`. Run everything from `kith/server`.
- Run a single test: `.venv/bin/python -m pytest tests/test_X.py::test_name -q` (from `kith/server`).
- Full gate before declaring a task done: `./check server` (from `kith/`) — runs `ruff check`, `ruff format --check`, `pytest`.
- Tests use real temp SQLite DBs via autouse fixtures (`isolated_tuning`, `never_the_real_database`, `no_stray_autonomy_loops`). Set tunables with `tuning.apply({...})`; get a fresh agent DB with the `db` fixture; build a runner with the `runner_on(db, monkeypatch)` helper pattern (reach the module via `sys.modules["kith.autonomy.runner"]`).
- Fake the model by stubbing `stream_agent` in the runner module, or replacing `agent_loop._stream_once`. The `_stream_once` event contract is `{"type":"turn","content":str,"tool_calls":list,"stats":dict}` (+ optional `delta`/`error`).
- **SOURCE-COUPLED TEST HAZARD:** `tests/test_the_last_round_costs_what_the_others_did.py` asserts on `inspect.getsource(...)`. It requires these literal strings to remain:
  - in `stream_agent`: `schemas: list[dict] = []` and `_final_answer(convo, config, host, schemas)`; and `tool_schemas(agent_db_path)` must NOT appear in non-comment code.
  - in `AutonomyRunner._step`: `tick_cap if wanted <= 0 else min(wanted, tick_cap)` (runner.py:539).
- Keep `_focus_prompt(detail, active)`'s signature intact — `test_a_session_owns_its_project.py` monkeypatches it as `lambda focus, active: ...`.
- Every task ends with a commit: `git -C /Users/saifullahsaeed/Desktop/personal/ai-fun/kith add <paths> && git -C ... commit -m "..."`.
- **Pre-flight (do once before Task 0):** `git -C /Users/saifullahsaeed/Desktop/personal/ai-fun/kith status` — Kith may have uncommitted edits to its own server. If the tree is dirty, stash or commit that WIP under a clear message first so our changes are isolated and reviewable. Do not discard it.

---

## Layer 0 — Seatbelt (ship first; makes every later layer safe to iterate on)

### Task 0.1: Per-session token budget with auto-rest kill-switch

A working session accumulates `tokens_in` across its ticks; when it crosses a ceiling, the session is set to rest and a durable message explains why. This is the one change that guarantees the 57M scenario can never recur unnoticed, even if every other fix is incomplete.

**Files:**
- Modify: `kith/domain/tuning.py` (append one `Tunable` to `TUNABLES`)
- Modify: `kith/autonomy/runner.py` (`AutonomyRunner.__init__`, `keep_working`, `_step` after the token rollup ~line 624)
- Test: `kith/server/tests/test_a_session_stops_at_its_budget.py` (new)

**Interfaces:**
- Consumes: `tuning.value("session_token_cap")`, `repo.conversations.set_working(path, conversation_id, False)`, `repo.messages.add_message(path, body, kind=..., link=...)`, `self._emit(kind, text, conversation=...)`.
- Produces: in-memory `self._session_tokens: dict[str, int]` keyed by `conversation_id`; reset in `keep_working`. Runner status unchanged (additive only).

**Design decision (write it down):** the accumulator is **in-memory**, reset when a session (re)enters `keep_working`. This covers the real failure (one uninterrupted multi-hour run). A server restart mid-run resets the counter — that is a known, bounded gap; a durable per-conversation column is a later hardening task (Layer 6 backlog), not needed to stop the headline bug.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_a_session_stops_at_its_budget.py
"""A working session that spends past its token ceiling is put to rest, once, with a durable note."""
import sys
from kith.services import tuning
from kith.infra.db import repositories as repo


def _runner_on(db, monkeypatch):
    module = sys.modules["kith.autonomy.runner"]
    monkeypatch.setattr(module, "AGENT_DB_PATH", db)
    return module.AutonomyRunner()


def test_crossing_the_token_cap_rests_the_session_and_leaves_a_note(db, monkeypatch):
    tuning.apply({"session_token_cap": 1000})
    conv = repo.conversations.start(db, "build a thing")  # returns id or row; adapt to real signature
    conv_id = conv["id"] if isinstance(conv, dict) else conv
    repo.conversations.set_working(db, conv_id, True)

    r = _runner_on(db, monkeypatch)
    # simulate the per-tick accounting hook directly (unit-level, no model call)
    r._charge_session(conv_id, tokens_in=600)
    assert repo.conversations.working_sessions(db)  # still working after 600 < 1000
    r._charge_session(conv_id, tokens_in=600)       # now 1200 > 1000
    assert not any(s["id"] == conv_id for s in repo.conversations.working_sessions(db))
    notes = repo.messages.list_messages(db, limit=5)
    assert any("budget" in m["body"].lower() for m in notes)


def test_re_entering_keep_working_resets_the_meter(db, monkeypatch):
    tuning.apply({"session_token_cap": 1000})
    conv = repo.conversations.start(db, "build a thing")
    conv_id = conv["id"] if isinstance(conv, dict) else conv
    r = _runner_on(db, monkeypatch)
    r.keep_working(conv_id)
    r._charge_session(conv_id, tokens_in=900)
    r.keep_working(conv_id)                          # explicit restart clears the meter
    assert r._session_tokens.get(conv_id, 0) == 0
```

- [ ] **Step 2: Run it, verify it fails** — `.venv/bin/python -m pytest tests/test_a_session_stops_at_its_budget.py -q` → FAIL (`_charge_session` / `_session_tokens` missing). Adjust `repo.conversations.start` call to the real signature discovered while implementing.

- [ ] **Step 3: Add the tuning knob** — append to `TUNABLES` in `kith/domain/tuning.py` (group `rhythm`, so `test_every_group_has_at_least_one_knob` stays satisfied; help > 60 chars; int with min<max):

```python
Tunable(
    key="session_token_cap",
    env="KITH_SESSION_TOKEN_CAP",
    label="Tokens one working session may spend",
    help="Total prompt tokens a single 'keep working' session may run through before it "
    "stops itself and tells you. This is the ceiling that turns a stuck all-nighter into a "
    "message at 8am; set it too low and long honest jobs stop early, too high and a loop can "
    "still cost real money before anyone looks.",
    default=5_000_000,
    group="rhythm",
    minimum=100_000,
    maximum=200_000_000,
    unit="tokens",
),
```

- [ ] **Step 4: Add the accumulator + charge/check** in `kith/autonomy/runner.py`:
  - In `__init__`: `self._session_tokens: dict[str, int] = {}`
  - In `keep_working`, after `set_working(..., True)`: `self._session_tokens[conversation_id] = 0`
  - Add method:

```python
def _charge_session(self, conversation_id: str, tokens_in: int) -> None:
    """Add this tick's prompt volume to the session's meter; rest it once past the cap.

    tokens_in (not uncached) on purpose: it is the volume the dashboard shows and the number
    a person reacts to. Reuses the exact set_working(False) baton that rest()/idle already use,
    so the loop simply stops selecting this session next tick.
    """
    if not conversation_id:
        return
    total = self._session_tokens.get(conversation_id, 0) + int(tokens_in)
    self._session_tokens[conversation_id] = total
    cap = int(tuning.value("session_token_cap"))
    if total < cap:
        return
    try:
        repo.conversations.set_working(AGENT_DB_PATH, conversation_id, False)
        repo.messages.add_message(
            AGENT_DB_PATH,
            f"I stopped this session — it ran through {total:,} tokens (its budget is {cap:,}). "
            "I've rested it so it can't keep spending while you're away. Tell me to keep going "
            "if you want more, or raise the session budget in settings.",
            kind="stuck",
        )
        self._emit("done", f"budget reached: {total:,} tokens this session — resting",
                   conversation=conversation_id)
    except Exception:
        pass
    self._session_tokens.pop(conversation_id, None)
```

- [ ] **Step 5: Call it from `_step`** — immediately AFTER the cumulative rollup (`runner.py:619-624`, the `self._tokens_in += tick_in` block), add: `self._charge_session(conversation_id, tick_in)`. Do NOT touch the `tick_cap if wanted <= 0 else min(wanted, tick_cap)` line — the source-coupled test needs it verbatim.

- [ ] **Step 6: Run the new test + the runner-sensitive suite**

```bash
.venv/bin/python -m pytest tests/test_a_session_stops_at_its_budget.py tests/test_a_session_that_keeps_working.py tests/test_the_last_round_costs_what_the_others_did.py tests/test_tuning.py -q
```
Expected: PASS. (If `test_a_session_that_keeps_working` flips on a `working` assertion, confirm the cap default in that test's world is high enough not to trip — it should be, at 5M.)

- [ ] **Step 7: Update the mislabeled cost knob's help** — in `kith/domain/tuning.py`, `tick_max_tokens.help` currently claims to be "the main lever on what he costs". It governs output only (~2% of spend). Change the last sentence to: `"It bounds a step's written output; the real ceiling on session cost is 'session_token_cap'."` (keeps help > 60 chars; no behavior change.)

- [ ] **Step 8: Commit**

```bash
git -C /Users/saifullahsaeed/Desktop/personal/ai-fun/kith add server/kith/domain/tuning.py server/kith/autonomy/runner.py server/tests/test_a_session_stops_at_its_budget.py
git -C /Users/saifullahsaeed/Desktop/personal/ai-fun/kith commit -m "feat(autonomy): per-session token budget with auto-rest kill-switch"
```

---

## Layer 1 — The baton (root fix: let a tick see what it already did)

### Task 1.1: Deepen and reframe the handoff in `_focus_prompt`

Today `_focus_prompt` surfaces the last **3** journal entries as neutral "last steps". Three is too shallow to reveal a 20-long loop, and neutral framing doesn't prompt a change of course. Deepen to a tunable count and frame it as "if these look the same, change approach or escalate — don't repeat."

**Files:**
- Modify: `kith/autonomy/prompts.py` (`_focus_prompt`, ~lines 58-63)
- Modify: `kith/domain/tuning.py` (new `handoff_steps` knob)
- Test: `kith/server/tests/test_the_handoff_shows_recent_attempts.py` (new)

**Interfaces:**
- Consumes: `repo.journal.list_journal(AGENT_DB_PATH, n)`, `tuning.value("handoff_steps")`.
- Produces: same `_focus_prompt(detail, active) -> str` signature (unchanged — do not break the monkeypatch in `test_a_session_owns_its_project.py`).

- [ ] **Step 1: Failing test**

```python
# tests/test_the_handoff_shows_recent_attempts.py
"""The work prompt surfaces enough recent attempts, framed as a loop warning."""
from kith.autonomy import prompts
from kith.services import tuning
from kith.infra.db import repositories as repo
from kith.config import AGENT_DB_PATH  # patched to temp db by autouse fixture


def test_focus_prompt_shows_the_configured_number_of_recent_steps(db, monkeypatch):
    monkeypatch.setattr(prompts, "AGENT_DB_PATH", db)
    tuning.apply({"handoff_steps": 6})
    for i in range(8):
        repo.journal.add_journal(db, f"(start) attempt {i}: db:generate still failing [used: shell]")
    detail = {"id": 96, "goal": "Make db:generate pass", "priority": "high", "checklist": []}
    text = prompts._focus_prompt(detail, [detail])
    # 6 most-recent attempts present, oldest of the 8 (0,1) absent
    assert "attempt 7" in text and "attempt 2" in text
    assert "attempt 1" not in text
    # framed as a loop warning, not neutral history
    assert "same" in text.lower() or "change" in text.lower() or "going in circles" in text.lower()
```

- [ ] **Step 2: Run, verify FAIL** — `.venv/bin/python -m pytest tests/test_the_handoff_shows_recent_attempts.py -q`.

- [ ] **Step 3: Add `handoff_steps` knob** in `kith/domain/tuning.py` (group `memory`):

```python
Tunable(
    key="handoff_steps",
    env="KITH_HANDOFF_STEPS",
    label="Recent steps shown when resuming",
    help="How many of his own last steps a working tick is shown, so it can tell it has "
    "already tried this and change course instead of repeating. Too few and a long loop is "
    "invisible to him; too many and the resume prompt gets expensive.",
    default=8,
    group="memory",
    minimum=1,
    maximum=30,
    unit="steps",
),
```

- [ ] **Step 4: Edit `_focus_prompt`** (`kith/autonomy/prompts.py`, replace the 3-line journal block ~58-63):

```python
    # Your own recent train of thought, framed as a loop check — so you can see when you
    # are re-attempting the same thing and change course or escalate, instead of repeating.
    recent = repo.journal.list_journal(AGENT_DB_PATH, tuning.value("handoff_steps"))
    if recent:
        lines.append("Your recent steps (most recent first) — if these look like the same "
                     "attempt again, STOP: change approach decisively, or say what's blocking "
                     "you and hand it back. Repeating it costs money and moves nothing:")
        lines += [f"  · {e['entry'][:200]}" for e in recent]
```
Add `from kith.services import tuning` at the top of `prompts.py` if not already imported.

- [ ] **Step 5: Run test → PASS**, then the prompt-sensitive neighbours:
```bash
.venv/bin/python -m pytest tests/test_the_handoff_shows_recent_attempts.py tests/test_roadmap.py tests/test_a_session_owns_its_project.py tests/test_tuning.py -q
```

- [ ] **Step 6: Commit** — `... commit -m "feat(autonomy): deeper, loop-aware handoff of recent attempts into each tick"`

### Task 1.2: Make the recorded step say attempt + outcome + next step

The auto-journal (`runner.py:636-642`) records the tick's final prose, which tended to be "what's still missing" (state), not "what I tried → what happened → next step" (continuity). Steer the closing summary via the WORK directive so the baton carries the right content.

**Files:**
- Modify: `kith/autonomy/directives.py` (the `WORK` directive text)
- Test: `kith/server/tests/test_work_directive_asks_for_attempt_shape.py` (new, lightweight — asserts the directive names the three parts)

- [ ] **Step 1: Failing test**

```python
# tests/test_work_directive_asks_for_attempt_shape.py
from kith.autonomy import directives

def test_work_directive_asks_to_record_attempt_outcome_next():
    d = directives.WORK.lower()
    assert "tried" in d and ("result" in d or "happened" in d or "outcome" in d)
    assert "next" in d
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Edit `directives.WORK`** — append a sentence instructing the end-of-tick summary shape, e.g.:
```
End every step by writing one short line of the form: what I TRIED · what HAPPENED (especially what failed and why) · the ONE next step. That line is what the next step sees first — record what you attempted and learned, not just what is still missing, or the next step will just rediscover the gap and repeat you.
```
(Match the file's existing directive prose style; keep it tight.)

- [ ] **Step 4: Run → PASS.** Then `.venv/bin/python -m pytest tests/test_instructions_name_real_tools.py -q` (directive text must not reference non-existent tools).

- [ ] **Step 5: Commit** — `... commit -m "feat(autonomy): steer the step summary to attempt/outcome/next-step so the baton carries continuity"`

---

## Layer 2 — Leanness (cheap ticks once memory carries forward)

### Task 2.1: Lower the live tool-output window default

`live_tool_chars` default is 240,000 (~65k tokens re-sent every round) — the direct driver of ~700k-token ticks. It was sized to avoid within-tick re-fetching under weak memory; with the baton improved, a smaller window is safe. Lower the default; keep the knob so a big-context user can raise it.

**Files:**
- Modify: `kith/domain/tuning.py` (`live_tool_chars.default`)
- Test: `kith/server/tests/test_a_picture_does_not_stay_forever.py` already drives these knobs with explicit values, so it is unaffected; add one assertion of the new default.

- [ ] **Step 1: Failing test** — add to a new small test `tests/test_the_live_window_default_is_lean.py`:
```python
from kith.services import tuning
def test_live_tool_chars_default_is_lean():
    tuning.reset(["live_tool_chars"])
    assert tuning.value("live_tool_chars") == 80_000
```

- [ ] **Step 2: Run → FAIL** (still 240k).

- [ ] **Step 3: Change default** `240_000` → `80_000` in `kith/domain/tuning.py` (still within [4_000, 4_000_000]; ~22k tokens live — ample for a coding tick with `keep_full_tool_results=6` protecting the newest).

- [ ] **Step 4: Run → PASS**, then `.venv/bin/python -m pytest tests/test_a_picture_does_not_stay_forever.py tests/test_the_prompt_is_cacheable.py tests/test_tuning.py -q`.

- [ ] **Step 5: Commit** — `... commit -m "perf(autonomy): lower live tool-output window default 240k→80k chars"`

---

## Layer 3 — Honest stall & error handling

### Task 3.1: A third stall signal — same focus task, no net progress across N ticks

The loop evaded detection because outcomes were reworded each tick (prose miss) and it occasionally called an `ADVANCE_TOOLS` tool. Add a signal that does not depend on wording or shape: if the same focus task has been worked for N ticks with no NET new checklist item ticked and no new deliverable, force breakout/escalation.

**Files:**
- Modify: `kith/autonomy/runner.py` (`__init__`, `_step`/`_detect_stall`)
- Modify: `kith/domain/tuning.py` (new `focus_grind_limit` knob)
- Test: `kith/server/tests/test_a_task_that_never_ticks_off_escalates.py` (new)

**Interfaces:**
- Consumes: `repo.tasks.task_detail(path, id)` → `checklist` (list of `{done}`), `deliverables`; `tuning.value("focus_grind_limit")`; existing `_give_up(active)` (prompts.py:175) which sets the task to `waiting` and notifies.
- Produces: `self._focus_task_id: int | None`, `self._focus_ticks: int`, `self._focus_progress: int` (checked-count + deliverable-count baseline) on the runner.

- [ ] **Step 1: Failing test** — drive `_step` with a stubbed `stream_agent` (yields nothing) so no real model runs, pre-seed a focus task with a checklist whose done-count never changes across ticks, and assert that after `focus_grind_limit` ticks the task is moved to `waiting` (via `_give_up`) and a "stuck" message exists. Use the `runner_on` helper and `monkeypatch.setattr(module, "stream_agent", lambda *a, **k: iter(()))`. (Model the setup on `test_a_session_that_keeps_working.py`.)

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Add `focus_grind_limit` knob** (group `stalls`, int, default 6, min 2 max 50, help > 60 chars: "How many ticks the same task may be worked with nothing newly ticked off or delivered before he stops and hands it back — the backstop for a loop that reworks its wording each time so the other detectors miss it.").

- [ ] **Step 4: Implement the counter** in `_step`: after the focus task is chosen (the `active` branch), compute `progress = (#checked checklist items) + (#deliverables)` from `task_detail`. If `focus_task_id` unchanged AND `progress` unchanged → `self._focus_ticks += 1` else reset to 0 and update baseline. After the tick, if `self._focus_ticks >= tuning.value("focus_grind_limit")` → call the same escalation `_give_up(active)` path used by stall-giveup, reset the counter. Keep it independent of the prose/shape `_detect_stall` so a reworded loop still trips it.

- [ ] **Step 5: Run → PASS**, then `.venv/bin/python -m pytest tests/test_stall_detection.py tests/test_a_session_that_keeps_working.py tests/test_idle_reasons.py tests/test_tuning.py -q`.

- [ ] **Step 6: Commit** — `... commit -m "feat(autonomy): escalate a task that grinds N ticks with no net progress"`

### Task 3.2: Don't let provider errors count as attempts / spin the loop

A 502 mid-stream produced a 0-token "error" tick. Ensure an errored tick (a) does not increment the no-progress grind counter as if it were a real attempt, and (b) backs the loop off briefly so a flapping provider isn't hammered.

**Files:**
- Modify: `kith/autonomy/runner.py` (`_step` error path; `_loop` gap logic)
- Test: `kith/server/tests/test_a_provider_error_backs_off.py` (new)

- [ ] **Step 1: Failing test** — stub `stream_agent` to yield `{"type":"error","message":"502"}`; assert the grind counter is NOT incremented by an error tick, and `_last_error_at`/backoff state is set.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** — when `error_msg` is set for a tick: skip the `_focus_ticks` increment (an error is not evidence of a loop); set `self._last_error_mono = time.monotonic()`; in `_loop`, extend the effective gap to `max(min_gap, backoff)` for a short window after an error (e.g. 15s). Keep it small and bounded.
- [ ] **Step 4: Run → PASS**, then `.venv/bin/python -m pytest tests/test_a_session_that_keeps_working.py -q`.
- [ ] **Step 5: Commit** — `... commit -m "fix(autonomy): provider errors don't count as attempts and briefly back off the loop"`

---

## Layer 4 — Planning quality at the one chokepoint (`add_task` handler)

All three gates live in the `add_task` tool handler (`kith/tools/tasks.py:178`), NOT the repo — so UI-created tasks stay unconstrained, exactly how `_verify_done` scopes itself. Return a structured refusal in the shape the model already understands from `_verify_done`.

### Task 4.1: Require a checkable done-condition for tasks that belong to real work

A task filed under a project or milestone must carry a `description` (what "done" looks like) of real substance, or it's refused with guidance. Trivial standalone tasks (no project/milestone) are left alone — mirroring `_verify_done`'s `_VERIFY_MIN_BRIEF` proportionality.

**Files:**
- Modify: `kith/tools/tasks.py` (`add_task` handler + its `@tool` schema/description)
- Test: `kith/server/tests/test_a_real_task_needs_a_done_condition.py` (new)

- [ ] **Step 1: Failing test**
```python
# tests/test_a_real_task_needs_a_done_condition.py
from kith.tools import tasks
from kith.infra.db import repositories as repo

def test_task_under_a_project_without_a_done_condition_is_refused(db):
    proj = repo.projects.add_project(db, "Warehouse", "", None)
    res = tasks.add_task(db, {"goal": "Verify the Prisma foundation", "project_id": proj["id"]})
    assert res.get("ok") is False
    assert "done" in (res.get("error") or "").lower()

def test_task_with_a_concrete_done_condition_is_accepted(db):
    proj = repo.projects.add_project(db, "Warehouse", "", None)
    res = tasks.add_task(db, {
        "goal": "Make db:generate pass",
        "description": "Done when `npm run db:generate` exits 0 and prisma/schema.prisma has the 6 models.",
        "project_id": proj["id"]})
    assert res.get("id")

def test_trivial_standalone_task_is_still_allowed(db):
    res = tasks.add_task(db, {"goal": "write ALPHA to alpha.txt"})
    assert res.get("id")
```
(Confirm `repo.projects.add_project` signature during implementation.)

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** in `add_task` handler — before `repo.tasks.add_task(...)`:
```python
    goal = args.get("goal") or ""
    description = (args.get("description") or "").strip()
    scoped = bool(args.get("project_id") or args.get("milestone_id"))
    _MIN_DONE = 40
    if scoped and len(description) < _MIN_DONE:
        return {
            "ok": False,
            "error": (
                "This task belongs to real work but has no checkable finish line. Add a "
                "'description' that says how you'll KNOW it's done — ideally something runnable "
                "(a command that exits 0, a test that passes, a file that exists), not a verb "
                "like 'verify' or 'inspect'. Then file it again."
            ),
        }
```
Update the `@tool` description/schema: change `description`'s schema note to "REQUIRED for tasks under a project/milestone: how you'll know it's done, ideally a runnable check." (Keep `required=("goal",)` so unscoped trivia still works.)
- [ ] **Step 4: Run → PASS**, then `.venv/bin/python -m pytest tests/test_a_session_owns_its_project.py tests/test_task_rollup.py tests/test_the_client_types_are_not_lying.py tests/test_tool_registry.py -q` (adoption + return-shape + registry must stay green).
- [ ] **Step 5: Commit** — `... commit -m "feat(planning): tasks under real work must declare a checkable done-condition"`

### Task 4.2: Reject/merge a near-identical open task (dedup)

Before inserting, compare the new goal against open tasks scoped to the same project/milestone; on a strong match, return the existing task's id with a "duplicate" note instead of creating a second one. Kills the 106≈110 / 107≈111 explosion.

**Files:**
- Modify: `kith/tools/tasks.py` (`add_task` handler)
- Test: `kith/server/tests/test_add_task_dedupes.py` (new)

**Interfaces:** reuse `kith.domain.stall.signature` + `similar` (pure Jaccard) — no new dependency, already tuned. Scope candidates with `repo.tasks.list_tasks(path)` filtered to open statuses and same `project_id`/`milestone_id`.

- [ ] **Step 1: Failing test** — create task "Canonicalize the Prisma client wiring" under a project; then `add_task` "Consolidate the Prisma client and server wiring" under the same project; assert the second returns the FIRST task's id with a `duplicate`/`merged` note and that `list_tasks` shows only one.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** — after validation, before insert:
```python
    from kith.domain import stall
    new_sig = stall.signature(goal + " " + description)
    for existing in repo.tasks.list_tasks(path):
        if existing["status"] in ("done", "dropped"):
            continue
        if existing.get("project_id") != args.get("project_id"):
            continue
        if stall.similar(new_sig, stall.signature(existing["goal"] + " " + (existing.get("description") or ""))):
            return {"ok": True, "id": existing["id"], "duplicate": True,
                    "note": f"Merged into existing open task #{existing['id']} — near-identical goal."}
```
- [ ] **Step 4: Run → PASS**, then re-run the Layer-4 neighbour suite from 4.1 Step 4.
- [ ] **Step 5: Commit** — `... commit -m "feat(planning): dedupe near-identical open tasks at creation"`

### Task 4.3: Cap tasks per milestone breakdown

When `milestone_id` is set, refuse to add beyond a cap of open tasks under that milestone (the breakdown-prompt says "one sitting each" but prompt limits are advisory — enforce at the handler, per the codebase's own lesson).

**Files:**
- Modify: `kith/tools/tasks.py` (`add_task` handler)
- Modify: `kith/domain/tuning.py` (new `milestone_task_cap` knob, group `turn`, default 6, min 2 max 20)
- Test: `kith/server/tests/test_milestone_breakdown_is_capped.py` (new)

- [ ] **Step 1: Failing test** — add `milestone_task_cap`=3 via `tuning.apply`; add 3 valid tasks under one milestone; assert the 4th is refused with a cap message.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** — when `milestone_id` set, count open tasks under it (pattern from `repositories/projects.py:381-384`); if `>= tuning.value("milestone_task_cap")`, return `{"ok": False, "error": "This milestone already has N open tasks — finish or drop some before adding more; plan one milestone shallowly, not all of it at once."}`. Add the knob.
- [ ] **Step 4: Run → PASS**, then `.venv/bin/python -m pytest tests/test_roadmap.py tests/test_tuning.py -q`.
- [ ] **Step 5: Commit** — `... commit -m "feat(planning): cap open tasks per milestone to force shallow breakdown"`

---

## Layer 5 — Right foot (propose the plan, invite review, ban verb-tasks)

### Task 5.1: Chat proposes a plan with done-conditions and invites review; discourage standalone verify/inspect tasks

Two prompt-level changes so work starts right: (1) the chat directive that lays out project structure must give each first-milestone task a checkable done-condition and then explicitly tell the person the plan is ready for them to start (rather than silently proceeding); (2) both chat and work directives state that "verify/inspect/consolidate X" is not a task — the check is a task's done-condition, not its own task.

**Files:**
- Modify: `kith/api/routes/chat.py` (`CHAT_DIRECTIVE`, ~line 48-58)
- Modify: `kith/autonomy/directives.py` (`WORK` — one line banning verb-only tasks)
- Test: `kith/server/tests/test_directives_shape_good_plans.py` (new, lightweight text assertions)

- [ ] **Step 1: Failing test**
```python
# tests/test_directives_shape_good_plans.py
from kith.autonomy import directives
from kith.api.routes import chat

def test_chat_directive_requires_done_conditions_and_invites_review():
    d = chat.CHAT_DIRECTIVE.lower()
    assert "done" in d
    assert "review" in d or "start it" in d or "say the word" in d

def test_directives_discourage_verb_only_tasks():
    combined = (directives.WORK + " " + chat.CHAT_DIRECTIVE).lower()
    assert "verify" in combined and ("not a task" in combined or "done-condition" in combined or "done condition" in combined)
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Edit `CHAT_DIRECTIVE`** — extend the "give it a home first" step: each first-milestone task must carry a checkable done-condition; a bare "verify/inspect X" is not a task (fold the check into the build task's done-condition); after laying out the project + first milestone's tasks, summarise the plan in one short message and invite the person to review/start it rather than assuming it should run unattended.
- [ ] **Step 4: Edit `directives.WORK`** — add one line: a task is an outcome with a checkable finish line; "verify/inspect/consolidate X" is a done-condition, not a task of its own.
- [ ] **Step 5: Run → PASS**, then `.venv/bin/python -m pytest tests/test_instructions_name_real_tools.py tests/test_a_chat_turn_is_on_the_record.py -q`.
- [ ] **Step 6: Commit** — `... commit -m "feat(planning): chat proposes done-condition'd plans and invites review; ban verb-only tasks"`

---

## Layer 6 — Backlog (hardening; do after the above lands and is verified in a real run)

- **Durable per-session spend** (survives a mid-run server restart): add `tokens_spent INTEGER DEFAULT 0` to the `conversations` table via a migration; increment in `_charge_session`; reset in `keep_working`. Replaces the in-memory dict from Task 0.1.
- **Semantic dedup** (Task 4.2 upgrade): swap Jaccard for `infra/db/vectors.cosine` over embeddings when `nomic-embed-text` is present, to catch reworded-but-identical goals the word-overlap check misses.
- **Full end-to-end verification**: start a real "keep working" session on a small app in a scratch dir, watch the Mind feed + `tick_log`, confirm per-tick `tokens_in` dropped to lean levels and the session either finishes or rests/escalates well under `session_token_cap`.

---

## Self-Review (coverage of the two fix lists)

**Execution list:** ①feed journal back → 1.1 · ②handoff says the right thing → 1.2 · ③turn down window → 2.1 · ④project map in baton → folded into 1.2 (WORK directive keeps a short "map" line) · ⑤session budget → 0.1 · ⑥honest stall → 3.1 · ⑦dedupe tasks → 4.2 · ⑧cap re-attempts → 3.1 + 0.1 · ⑨provider backoff → 3.2 · ⑩re-aim cost knob → 0.1 Step 7. ✅

**Planning list:** ①checkable done-condition → 4.1 · ②outcome not activity → 4.1 + 5.1 · ③kill verify-task category → 5.1 · ④dedupe → 4.2 · ⑤cap breakdown → 4.3 · ⑥human right-foot approval → 5.1 · ⑦plan shallow/gate deep → 4.3 + 5.1 (deps already exist) · ⑧escalate not spawn → 3.1 + 4.2. ✅

**Execution order (safe):** 0.1 → 1.1 → 1.2 → 2.1 → 3.1 → 3.2 → 4.1 → 4.2 → 4.3 → 5.1. Seatbelt first; leanness only after the baton; planning gates are independent and can slot anywhere after Layer 0.
