# Workflow steps — keep adding agents to the same run

*2026-09-30*

## Why

Workflow mode has been on in two chats (Ollama TUI, 2026-09-28; PR reviewer, 2026-09-29/30):
19 turns, ~495 tool calls, 7 turns of 30+ calls — and **zero** `run_workflow` calls. Two causes,
both read off the transcripts:

1. **Every text about the mode argues against using it.** The `[Right now]` line
   (`services/memory_context.py`) says "you *may*… do small or tightly coupled work yourself; a
   fan-out on a one-turn task is wasted money". The tool description opens with "Only worth it
   when the work really splits; do small work yourself". `persona/30-how-you-read.md` says "it is
   your judgement, not an order… do small or tightly coupled work yourself". Even the `/workflow`
   line says to skip it if small. The one time he weighed it (2026-09-29 19:51) he quoted that
   back: *"this is fairly linear… Not a fan-out. Let me do it myself."*
2. **The only way in is a whole script, written up front.** Both chats were linear loops on one
   repo — build, run, fix, verify. A script fits a fan-out planned in advance; it does not fit
   "do this, look at what came back, decide the next thing", which is how he actually works. By
   the tool's own definition he was right not to use it.

The person asked for a **stepper**: a workflow Kith keeps going by adding more agents as the
last ones finish. And decided (2026-09-30) that **Kith still decides** when to use agents — no
tools taken away in workflow mode.

## Decisions

| Question | Decision |
|---|---|
| Who decides to use agents | Kith, in workflow mode (or after `/workflow`). No tools withheld. |
| Shape | A run is a sequence of **steps**; each `add_agents` call is one step |
| Step format | A JSON list of agents, not code — nothing to rehearse, nothing to crash |
| Scripts | Stay. A finished script run can be continued with steps |
| Wording | The four texts are rewritten to say what the mode means: work goes to agents by default |

## The tool: `add_agents`

```
add_agents(
  agents=[{label, kind, prompt, after, model, schema}, ...],
  run="wf-…",       # add to this run; omit to start a new one
  name="…",         # a new run's title
  budget_usd=5,     # a new run's ceiling, across all its steps
  wait=false,       # hold the turn until this step finishes (≤ 4 min), as run_workflow
)
```

Each item:

- `label` — required, unique within the run. How later items and later steps refer to it.
- `kind` — `scout` (default), `builder`, `verifier`, as today; plus `merge` and `revise`:
  - `merge`: `after` names the builders to merge; optional `tests: true`. Same as `merge()`.
  - `revise`: `after` names one builder; `prompt` is the feedback. Same as `revise()`.
- `after` — labels of any agent in this run, **earlier steps or this one**. It means "builds on
  what that agent produced":
  - after a builder, merge or revision → starts from a copy with its patch applied (as today);
  - after a scout or verifier → its report is appended to this agent's prompt.

  So `survey → builder after survey → verifier after builder` needs no glue code — the thing
  four of seven past scripts got wrong (`agent(fn())` sending a report as a prompt).
- `model`, `schema` — as `agent()` today.

**Scheduling inside a step.** Items with nothing unfinished in `after` start at once (up to the
six-at-once cap); each one starts as soon as the ones it names finish. Unknown labels, duplicate
labels and cycles are refused before anything starts, in the tool result. When an item fails,
whatever is `after` it is skipped and says why.

**It returns at once**, like `run_workflow`: `{run, step, agents: [labels], note}` — end the turn
saying what was sent. `wait=true` returns the step's result instead, for a short step.

## Run lifecycle

- A step is a phase: the run's `phases` gains `Step N`, so the agent map grows a column to the
  right per step with no new UI.
- While a step runs the run is `running`. When its last item ends the run is `finished` (or
  `failed`/`stopped`), its note is written, and the scheduler wakes the chat — exactly the path
  scripts use today.
- `add_agents(run=…)` on an ended run **reopens it**: `running` again, `reported` reset, a fresh
  halt switch, the next phase appended. Budget, the 30-agent cap and spend are cumulative across
  the run's steps; the 60-minute limit is per step.
- One step running per chat, as now (a second `add_agents` while one runs is refused with its id).
- `stop_workflow` stops the running step; the run can be continued afterwards.
- **Continuing after a restart.** The board is in memory; a step's results are already written
  to `<run>/agents/*.json` with their labels. A run the server no longer holds is rebuilt from
  its folder (as `_earlier_run` does for resume), and `run.json` records spend so far so the
  budget still holds.
- **Script runs.** The same folder layout, so `add_agents(run=<script run>)` works: the script's
  agents are addressable by label (the newest when a label repeats).
- Builders' copies are closed when a step ends. A `revise` in a later step rebuilds the copy
  from the patch — `_revise` already does this when the copy is gone.

## Who may

Same gate as `run_workflow`, plus the resume rule for continuations:

- A **new** run: only when the turn may start workflows (`/workflow` or workflow mode); in an
  unattended turn only in workflow mode, and within the existing three-in-a-row chain limit.
- **Continuing** a run this chat started: allowed without a fresh opt-in (the person agreed to
  the run), including in the turn a finished step wakes — that is the whole point. Bounded: at
  most **5 steps in a row with nobody watching**; past that it has to report and stop. The
  budget is the real ceiling.
- Agents cannot call `add_agents` (added to `workers.WITHHELD` beside `run_workflow`).

## What the woken turn reads

```
Step 2 of `pr-reviewer` (wf-3fa9c1) finished after 3m12s — 2 agents, $0.14
(run so far $0.31 of $5.00).

- builder "loop" — patch …/loop-a41c.patch (+212 −40)
- verifier "check-loop" — reported:
  <its report, cut to fit>

Nothing has been applied. …
Next: add_agents(run="wf-3fa9c1", agents=[…]) — later agents can go after any of:
survey, loop, check-loop. Or apply the patch and tell them.
```

Unlike a script run, a step has no `finish()` value, so the note carries the step's reports
themselves (scouts and verifiers whole up to the existing 6,000-character cap; the rest on disk).

## Wording

The mode's meaning flips from "permitted, discouraged" to "the default for real work, his call
when":

- **`[Right now]`, mode on:** workflow mode is on because they want work done by agents. For
  anything past a quick answer or a small edit, lead: send a builder for a change, a scout for
  wide reading, a verifier before calling it done — one agent is a fine first step — and add
  the next step when they report. Doing it all yourself is for small things.
- **`/workflow` line:** they asked for agents on this; use them.
- **Tool descriptions:** `add_agents` leads with the linear case ("one builder, then a verifier
  after it"); `run_workflow` drops "only worth it when the work really splits" and points at
  `add_agents` for step-by-step work.
- **`persona/30-how-you-read.md`** paragraph rewritten to match; **`running-a-project` SKILL**
  table gains the row "a change to build, in workflow mode → `add_agents` builder, then a
  verifier after it" — a named step in the procedure he already runs, which is what has moved
  him before.

## UI

- `tool-language` / `tool-row` / `tool-result`: `add_agents` drawn like `run_workflow` ("sent
  agents", the run card that opens the agent map); `run-strip` and `workflow-in-turn` count it.
- A new step publishes a `step` line carrying the whole phase list; the runs fold treats it like
  `started` without dropping the agents already drawn.

## Not doing

- No DAG language beyond `after`; loops and fan-out over a list stay scripts.
- No withholding of edit tools in workflow mode (the person's call, 2026-09-30).
- No new persistence table — the run folder is already the record.

## Testing

Pytest with `workers.run_agent` stubbed: label/cycle/unknown refusals; ordering inside a step;
report-into-prompt and patch-into-copy for `after`; failed dependency skips its dependents;
reopening a finished run (state, `reported`, phases, cumulative budget and cap); continuing a
script run by label; continuing after a simulated restart; the five-unwatched-steps bound; the
permission rules; the wake note. UI: vitest for the fold of a `step` line and the tool row.

**Measured after it ships:** one real build request in a workflow-mode chat. Success is Kith
sending a step unprompted and continuing it at least once (builder → verifier), counted from the
transcript the same way this spec's numbers were.
