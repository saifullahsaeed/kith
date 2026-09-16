# Sub-agent workers — builders, isolation, and workers that outlive a turn

*2026-09-15*

## Why

`delegate_subtask` shipped one kind of worker: an anonymous, read-only scout, on the main
model, given ten rounds, whose entire existence ended when it reported. That is a good tool and
the economics behind it hold — the finding is small and the finding out is not. But it is one
tool, and the four things that make Claude Code's agents powerful are all absent:

1. **One kind of worker.** `delegation.py` refused a `role` parameter, correctly, on the
   grounds that it would be "a schema the model has to choose between on every round, for a
   choice with one real option." That reasoning was right while there was one option and is
   the binding constraint now.
2. **Safety by amputation.** A whitelist caps a worker at "finder" forever. Claude Code's
   writing agents get safety from *isolation* instead — and isolation is also what lets them
   run in parallel.
3. **One model for everything.** Six parallel scouts were six streams of the most expensive
   model available.
4. **No continuation.** A report that was 80% right cost a whole second search to fix.

Worth recording what Claude Code does *not* do, because it shaped the design: by default its
subagents share the parent's working directory and write to the real tree, `isolation:
"worktree"` is opt-in per spawn, and when it is used the resulting worktree is simply left for
a human to integrate. Kith runs unattended turns, so "leave it for a human" is not available —
which is why the return path here is a patch and not a dangling branch.

## What was built

### Layer 0 — the isolation seam

One context variable. `session_context.working_from(directory)` pins a base; `paths.base_dir()`
checks it ahead of the project lookup, and `permissions._inside_pinned_worktree` reads the same
variable so the folder is a free zone.

This is the whole isolation mechanism, and it is ~30 lines because `paths.resolve()` is the
single funnel every path in the application goes through. A worker thread running in a
`copy_context()` with this set reads and writes its copy through every existing tool, and no
tool knows it moved.

The permission half is not optional. A worktree is not the linked project folder, so without
it every write raises a prompt — put to an empty room, because a worker has no `ask` and nobody
watches a scratchpad. In ask-mode that is indistinguishable from a hang.

### Layer 1 — `infra/workspace/worktrees.py`

A private checkout per builder, seeded with `git stash create` so the worker sees *uncommitted*
work rather than HEAD — a patch against a tree nobody has would not apply. Detached, always.
Its own index, which is what makes `git add -A` (the only way an untracked file reaches a diff)
safe to run inside it.

Copies live in `paths.internal()`, beside the databases. They were first placed under the
workspace root — which is outside the project, *unless the workspace root is itself the
repository*, which `ensure_repo` will make it. A test caught it.

Untracked files in the source tree are a documented gap: `stash create` does not carry them,
and `--include-untracked` writes an object for every untracked file in the tree.

### Layer 2 — workers as stored transcripts

Migration `v45_workers`, `models.Worker`, `repositories/workers.py`.

A worker is not a live thing. It is a row holding a message list, plus a function that runs
more rounds on it — which is why resume works across a turn and across a restart, through the
same code path as sending one. `_run_worker` takes an optional `carried` scratchpad; that one
parameter is the whole of persistence.

**What is deliberately not stored:** the middle. `agent_loop._run_turn` copies the message list
it is handed (`convo = list(messages)`), so the worker's actual reads are not reachable without
changing the loop's contract for every caller — and those reads are precisely what delegation
exists to keep out of anybody's context. A resumed worker knows its brief and its own
conclusion, not how it got there. Weak for a scout (the tool description says to send a fresh
errand instead); strong for a builder, whose real state was never in the transcript — it is the
edits in its copy, still on disk.

### Layer 3 — three tools, no enum

| Tool | Tools | Model | Rounds | Returns |
|---|---|---|---|---|
| `delegate_subtask` | `GIVEN` | `scout_model` (blank = main) | `subtask_rounds` (10) | prose |
| `send_builder` | `GIVEN ∪ WRITING` | main, not a knob | `builder_rounds` (16) | patch + prose |
| `follow_up` | the worker's own | the worker's own | the worker's own | either |

Plural tools rather than a `role` argument: the choice is which tool to call, not a schema the
model resolves on every round. `ROLES` holds the four differences as data.

`WRITING` is four tools. `WITHHELD` still applies to every role — no `commit`, `publish`,
`shell`, `run_tests`, board writes, `remember`, `ask`, or delegation of its own. No `shell` or
`run_tests` for a builder specifically because side effects are what would stop two builders
sharing a round, and the parallelism is worth more.

**No reviewer role.** A reviewer that reads a diff is a scout with the diff in its objective.
The original refusal to add a verifier survives intact.

### Layer 4 — the loop

`send_builder` and `follow_up` join `_PARALLEL_SAFE` — a builder *writes* and clears the
side-effect bar anyway, because of where it writes. That is the payoff for Layer 0: without
separate copies, builders would have to be serial. Both also join `_GATHERING_TOOLS`, so a
landing turn cannot open one.

## Known limits

- **Untracked source files** are not carried into a copy (above).
- **Overlapping builders** produce two patches that will not both apply. Said in the tool
  description; not enforced, because enforcing it means guessing which files an objective
  touches before it runs.
- **Scout follow-up is weak** by construction (above).
- **Pruning is lazy**, on the next `send_builder`, because this application has no housekeeping
  pass and adding a scheduler tick for one caller is a second mechanism.
