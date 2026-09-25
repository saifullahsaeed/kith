# Workflows — scripted, background multi-agent runs replace the three worker tools

*2026-09-25*

## Why

Kith's sub-agents were three tools the model had to choose to reach for on any round:
`delegate_subtask` (read-only scout), `send_builder` (edits in a worktree, returns a diff) and
`follow_up` (resume a stored worker). The measured record says the choice is never made:

- 25 `delegate_subtask` calls in all history, **every one immediately after being told to**.
- `send_builder`: zero calls across two runs whose prompts named independent parallel areas.
  The decision was lost one step before delegation — he framed the work as one inventory to
  walk, not as four jobs (`kith-builder-adoption-failure`).
- 15 of 18 delegating rounds sent exactly one errand, so the parallel fan-out never happened.

So the decomposition cannot be left to a per-round choice. Claude Code's answer is the
Workflow tool: a script that orchestrates agents deterministically — `agent()`, `parallel()`,
`pipeline()`, phases — which runs in the background and reports once. Crucially, it runs only
when **the person opts in**, so the question "should this fan out?" is answered by the person,
and the model's job becomes writing the decomposition down as code, which it is good at.

## Decisions (agreed in conversation)

| Question | Decision |
|---|---|
| Pattern | Workflow scripts, like Claude Code's Workflow tool |
| Who starts one | The person opts in; Kith writes the script |
| Old tools | Removed. Scout and builder survive as `agent(kind=...)` inside a script |
| While it runs | Background: the turn ends, the chat stays usable, a new turn brings the result |
| Script | Python, run confined, orchestration only |

## Opting in

A message that **starts with `/workflow`** opts that one turn in. That is the whole mechanism:
the prefix is the person's own text, so the transcript records it, the model reads it, the CLI
gets it for free (`kith send "/workflow …"`), and there is no hidden directive and no new
request field. The composer offers `/workflow` as a slash command that writes the prefix, and
the user bubble draws it as a chip.

`begin_turn` reads the opening message and sets `session_context.workflows_allowed()` for the
turn. `run_workflow` refuses without it — enforced in the handler, not asked for in prose. The
tool stays in every round's schema so the cached prefix never changes between turns. A turn the
scheduler opens (reminder, wake) never starts with the prefix, so it is never opted in.

## The script

Python. The helper (`server/workflow/kith_workflow.py`, stdlib only) is preloaded, so these
are plain globals:

```python
META = {"name": "port-rag", "description": "...", "phases": ["Survey", "Port", "Review"]}

with phase("Survey"):
    found = agent("List every package that imports old_rag.", schema={"packages": ["str"]})
with phase("Port"):
    done = parallel([lambda p=p: agent(f"Port {p} to new_rag.", kind="builder", label=p)
                     for p in found["packages"]])
with phase("Review"):
    review = agent("Review these patches for mistakes:\n" + "\n".join(d["diff"] for d in done if d))
finish({"review": review})
```

| Call | Returns |
|---|---|
| `agent(prompt, kind="scout"\|"builder", label=None, schema=None)` | scout: `str`, or a `dict` when `schema` is given. builder: `{"report", "diff", "patch_file"}` (`report` is a dict when `schema` is given). Raises `AgentFailed`. |
| `parallel([fn, ...])` | list of results, `None` where a call raised |
| `pipeline(items, *stages)` | each item through the stages independently; a stage returning `None` drops the item; failures become `None` |
| `phase(title)` | context manager; the panel shows the current phase |
| `log(text)` | a progress line |
| `finish(value)` | the result handed back (JSON-serialisable) |

`print()` goes to the run's log, never to the protocol channel.

A scout gets today's read-only set (`GIVEN`); a builder gets `GIVEN | WRITING` in its own git
worktree. Both run through the existing worker code (`tools/workers.py`, moved out of
`tools/delegation.py` unchanged in substance): briefs, per-role models and rounds, cost rows,
worktree isolation via `session_context.working_from`. `schema` appends a request to end the
report with one JSON object and parses it, with one tool-less repair round if it fails.

### Limits (server-side)

- 6 agents at once, 30 per run, per-agent round caps from the existing knobs.
- 60 minutes wall clock.
- One running workflow per conversation.
- Agents cannot start workflows (`run_workflow` is in `WITHHELD`).

## How it runs

1. **Validate in the turn.** The script is compiled and `META` read with `ast` before anything
   starts, so a syntax error comes back as the tool result and he fixes it in the same turn.
2. **Launch.** Files go to `<project>/.kith/scratch/workflows/<run>/` (gitignored, readable by
   him afterwards). The script runs on the server's own interpreter — `python -E -s` in a
   checkout, `kith-server --run-workflow` in the frozen app — under a `sandbox-exec` profile
   built by `infra/confinement.profile`: no network, no `$HOME` except the run folder and the
   interpreter, a constructed environment, the run folder as cwd. If this machine cannot confine
   a process, workflows refuse rather than run model-written code unconfined.
3. **Protocol.** JSON lines. The script writes requests (`agent`, `phase`, `log`, `finish`) on a
   private dup of stdout; the server answers `agent` requests on stdin. The server runs each
   agent on a pool capped at 6, with the conversation's context copied in and
   `nobody_watching()` set (an agent has no `ask`, and nobody answers a prompt mid-run).
4. **Return at once.** The tool answers `{run, name, phases}` and the turn ends normally.
5. **Progress.** `activity.feed` lines: `workflow` for run-level state, and the existing
   `errand` shape (plus `run`) for each agent, so the Work panel groups agents under their run.
6. **Stop.** `POST /api/workflows/<run>/stop` sets the run's event: the script is killed and
   every agent stops at its next event.
7. **Finish.** `result.json` and one `<label>.patch` per builder land in the run folder, and
   the run is reported through `services/workflows.finished_since_last_look()`, which
   `scheduler.wake_finished` reads beside background processes. His next turn opens with a note:
   the result (capped, with the file path), one line per agent, and the patch paths. **He
   applies the patches himself** (`git apply`), which is the diff-as-report rule builders
   already had. A crash, timeout or stop wakes him too, with what finished and why it ended.

Runs are in memory. A server restart ends the script (its stdin closes) and the run is lost;
the files already written stay.

## Deleted

- `delegate_subtask`, `send_builder`, `follow_up`, and `tools/delegation.py` as a tool module.
- `services/errands.py` and every use of it in `agent_loop.py` (the round-boundary drain, the
  end-of-turn wait, the `errand_back` / `waiting_on_errands` events).
- The `sending-workers` skill; delegation paragraphs in `persona/30-how-you-read.md`; the
  errand pointers in `read_file` / `grep` / `outline` / `shell` descriptions.
- Tests for the removed behaviour.

The `workers` table stays, unwritten (same precedent as milestones).

## UI

- `/workflow` slash command; the prefix drawn as a chip in the user bubble.
- `run_workflow` tool card: name, description, phases, script folded.
- The Work panel's Errands section becomes Workflows: run header (name, phase, state, Stop),
  agents under it with their live steps.
- `errand_back` rendering removed from the thread.

## Out of scope (v1)

Saved/named workflows in `.kith/workflows/`; resuming a run from cached agent results; choosing
a model per agent beyond the per-role model; a builder starting from another builder's copy;
surviving a server restart.

## Testing

- The confined script cannot read a real folder under the real `$HOME` and cannot reach the
  network (confinement's own lesson: temp dirs live outside the deny and prove nothing).
- `run_workflow` refuses without the `/workflow` prefix; a scheduler turn is never opted in.
- A script's syntax error comes back in the tool result; nothing is launched.
- The helper: `parallel` preserves order and turns failures into `None`; `pipeline` drops on
  `None`; `print` does not corrupt the protocol.
- Concurrency never exceeds the cap; the per-run agent cap refuses the 31st.
- Stop kills the script and its agents; a finished, failed or stopped run wakes the chat.
- A builder writes only inside its own copy; the role tool sets stay exhaustive.
