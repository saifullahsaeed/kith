# Workflow Steps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `add_agents` — a workflow Kith keeps going one step at a time, adding agents to the same run as the last ones report — and wording that stops telling him not to use workflows.

**Architecture:** A new tool module `server/kith/tools/workflow_steps.py` validates a step (a JSON list of agents), then runs it on a thread with a small dependency scheduler that calls the existing runners in `tools/workflow.py` (`_one_agent`, `_merge`, `_revise`) directly — no script subprocess. The run board (`services/workflows.py`) learns to reopen an ended run for another step and to restore one lost in a restart. The run folder's `run.json` is the step ledger: budget, spend, labels.

**Tech Stack:** Python 3 (Flask server, pytest), React/TypeScript UI (vitest).

**Spec:** `docs/superpowers/specs/2026-09-30-workflow-steps-design.md`

## Global Constraints

- Limits reused from `tools/workflow.py`: `AT_ONCE` 6, `MAX_AGENTS` 30 per run (cumulative across steps), `MAX_SECONDS` per step, `DEFAULT_BUDGET_USD` 5 / `MAX_BUDGET_USD` 50 (cumulative).
- At most **5** steps in a row with nobody watching (`MAX_STEPS_UNWATCHED = 5`).
- Kinds: `scout`, `builder`, `verifier`, `merge`, `revise`. Labels unique within a run.
- Tool descriptions total must stay under 32,000 chars (`test_the_whole_surface_stays_affordable`).
- Agents never get `add_agents` (`workers.WITHHELD`); a landing turn never starts one (`_STARTING_TOOLS`).
- Nothing a builder makes is applied to the real tree.

## Review Focus

- A step naming a label from a *script* run it continues — labels resolve from `agents/*.json`, newest wins.
- `after` a builder that changed nothing — no patch to apply, its report is carried instead; a merge of only such builders fails cleanly.
- Stop pressed mid-step — running agents give up, unstarted ones are reported as not started, the run can be continued afterwards.
- A server restart between steps — the run is rebuilt from its folder and its spend still counts against the budget.
- Two identical agents with different labels in one step — each keeps its own record (key includes the label).

---

### Task 1: The board can reopen and restore a run

**Files:** Modify `server/kith/services/workflows.py`; Test `server/tests/test_a_workflow_keeps_going_in_steps.py`

**Produces:** `Run.kind: str = "script"` (in `public()`); `start(..., kind="script")`; `reopen(run, phase) -> bool`; `restore(run_id, conversation, name, folder, phases, started, kind) -> Run`.

- [ ] Tests: reopening an ended run sets `running`, clears `reported`/`note`/`ended`, gives a fresh `halt`, appends the phase and publishes a `step` line with the whole phase list; refused while that run or another run of the chat is running; `restore` puts an ended, reported run on the board and returns the existing one if already there.
- [ ] Implement; run the tests; commit.

### Task 2: One gate for opening a new run

**Files:** Modify `server/kith/tools/workflow.py` (`run_workflow`, new `new_run_refusal`)

**Produces:** `new_run_refusal(conversation: str, unwatched: bool) -> str` — the three refusals `run_workflow` makes today for a new run (nobody watching without mode, too many unwatched in a row, not allowed), in the same order and words.

- [ ] Replace the inline checks in `run_workflow` with it. `_earlier_run` tolerates a folder with no `script.py` (a step run); `run_workflow(resume=<step run>)` refuses with "continue it with add_agents(run=...)".
- [ ] Existing suite `tests/test_a_workflow_runs_confined_in_the_background.py` passes unchanged; commit.

### Task 3: Reading and checking a step

**Files:** Create `server/kith/tools/workflow_steps.py` (`read_items`, `check_plan`); Test file from Task 1

**Produces:**
- `read_items(raw) -> tuple[list[dict], str]` — items `{label, kind, prompt, after: list[str], model, effort, schema, tests}` or the first problem.
- `check_plan(items, known: dict[str, dict]) -> str` — `known` is `{label: {"kind", "ok"}}` of earlier agents. Refuses duplicates (against `known` and within the step), `after` naming itself / an unknown label (listing the labels there are) / a failed earlier agent, merge or revise after a non-patching kind, and cycles.

- [ ] Tests for each refusal and for a valid chain across steps; implement; commit.

### Task 4: Running a step — `add_agents`

**Files:** Modify `server/kith/tools/workflow_steps.py`; Modify `server/kith/tools/__init__.py` (import); Test file from Task 1

**Consumes:** Task 1–3; `workflow._one_agent/_merge/_revise/_Agents/_Failed/_wait_for/_took/_ask_key`, `workers.project_brief`.

**Produces:** tool `add_agents(agents, run, name, budget_usd, wait)`; `_drive_step`; `_step_note`; ledger helpers `_ledger/_save_ledger/_records/_known_labels`.

Behaviour:
- New run (no `run`): `new_run_refusal`, then `workflows.start(..., phases=["Step 1"], kind="steps")`, folder `<project>/.kith/scratch/workflows/<id>/agents`, ledger written.
- Continue (`run`): this chat's run, not running; rebuilt with `workflows.restore` from `run.json` when the board lost it; refused after 5 unwatched steps in a row; `reopen(run, "Step N")`.
- Scheduling: an item starts when everything in its `after` has finished (this step) or exists (earlier steps); dependents of a failed item are skipped with the reason; stop/timeout halts the step.
- `after` → patches (value dicts with a diff) go to `after=` / `built=`; everything else is carried as a report appended to the prompt (≤ 8,000 chars each).
- Record key = sha256(label + `_ask_key`) so identical asks keep separate records.
- Ledger after the step: labels `ok`, `spent` (= `agents.live`), `sent`.
- Note: head with step, run, time, agents, step cost, run spend vs budget; one line per item with its outcome and report (6,000 chars shared); apply guidance; `Next: add_agents(run="…")` with the labels available.

- [ ] Tests (stubbed `workers.run_agent`, and `open_copy`/`worktrees` stubbed where a real copy is needed): refused without opt-in; new run returns at once and finishes with a note naming every label; a scout's report reaches the prompt of an agent `after` it, in the same step and in a later one; a builder's patch reaches a later agent's `after`; failure skips dependents; continuing reopens the same run with `Step 2` and cumulative spend/agent count; continuing a script run by label; continuing after `forget_all()` (restart); budget exhausted refuses new agents; 5 unwatched steps then refused; `wait=true` returns the note; two identical asks with different labels both resolvable.
- [ ] Implement; run; commit.

### Task 5: Wording and wiring

**Files:** `server/kith/services/memory_context.py`, `server/kith/tools/workflow.py` (description, closing note, `check_workflow` next), `server/persona/30-how-you-read.md`, `server/skills/running-a-project/SKILL.md`, `server/kith/tools/workers.py` (WITHHELD), `server/kith/services/agent_loop.py` (`_STARTING_TOOLS`), `server/tests/test_a_new_tool_is_not_born_orphaned.py`, `server/tests/test_a_sub_agent_cannot_reach_past_reading.py`, `server/tests/test_landing_can_finish_the_work.py`

- [ ] Mode line, `/workflow` line, persona paragraph and skill row say: agents are the default for real work; one builder is a fine first step; add the next step when they report.
- [ ] `run_workflow` description drops "Only worth it when the work really splits; do small work yourself" and points at `add_agents` for step-by-step work; a finished script run's note says it can be continued with `add_agents`.
- [ ] Tests: `add_agents` withheld from agents and from landing; something points at it; description budget holds; the mode line does not say "wasted money". Run the server suite; commit.

### Task 6: UI

**Files:** `ui/src/lib/tool-language.ts`, `ui/src/lib/tool-row.ts`, `ui/src/components/assistant-ui/tool-result.tsx`, `ui/src/components/assistant-ui/run-strip.tsx`, `ui/src/components/assistant-ui/workflow-in-turn.tsx`, `ui/src/components/chat/workflows.tsx`, `ui/src/lib/backend/activity.ts`; tests beside them

- [ ] `add_agents`: verb "sent agents", group workflow; row digest "Step N · labels"; the run card (reusing `WorkflowStarted`, no script) for its result; counted with `run_workflow` in the run strip and in-turn cards.
- [ ] `foldRuns` handles a `step` line: state back to running, `endedAt` 0, phases replaced; agents kept.
- [ ] vitest for the fold and the row; `tsc -b`; commit.

### Task 7: Verify

- [ ] `./check` (server + ui) passes; whole-branch review; fix what it finds.
