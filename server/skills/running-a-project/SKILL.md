---
name: running-a-project
description: Use whenever work is bigger than a single errand and needs durable state that survives being put down — setting up, resuming, triaging, sequencing, unblocking, reconciling or closing a project. Use it when a board may be stale or contradictory, when a job is too big for one sitting, or when deciding whether something is a project or a task.
---

# Running a project

Your projects and tasks are not a list you keep for your person's benefit. They are
how you decide what to work on next, and how work survives being put down and picked up in a
later conversation that remembers none of it. Set up badly they hand you the wrong thing, or
nothing at all, and are convincing about it.

**The board is a control state, not a progress report.** It has to tell a cold-started you what
is true, what is blocked, and what to do next — without making you reconstruct a story from
contradictory notes.

## Is this even a project

A one-off — read this, fix that, write one document, answer one question — is a task with no
project. A project is work with a concrete deliverable, several work packages,
real dependencies, spanning sittings. Do not create one because a task sounds important.

A project ends when its deliverable is done. It is not a promise to keep producing work, so do
not add maintenance or polish to keep it open.

A conversation locks to the first project it works on and stays there for life. That is not a
reason to avoid starting one mid-conversation; it is a reason to say plainly that a second,
unrelated project cannot be followed here.

## When records disagree

1. **Their current decisions** define accepted scope and override an obsolete plan.
2. **Current files and fresh runnable checks** define what exists.
3. **The approved brief and each task's plan** define what was asked for.
4. **Status, dependencies, checklists, deliverables** describe control state and evidence.
5. **Comments and `.kith/memory.md`** are history and indexes, never proof.

Never silently reconcile a contradiction — record the correction. A `done` status does not
outweigh a failing check, a missing deliverable or an unchecked required item. A stale memory
entry does not outweigh the repository. If evidence cannot settle it, stop and ask.

## Task states

Five, and **your person sets none of them.** You own every transition; the one decision that is
theirs is approving a plan.

```mermaid
stateDiagram-v2
    [*] --> planning
    planning --> approved: they approve the plan and its checklist
    planning --> dropped: cancelled before approval
    approved --> working: dependencies clear and work starts
    working --> done: every brief requirement met and verified
    working --> dropped: cancelled or superseded
    dropped --> [*]
```

- `planning` — brief, approach and checklist being grounded. Everything starts here. Do not implement.
- `approved` — they read the plan and said yes. `update_task` refuses this without both, and names what is missing.
- `working` — actively progressing.
- `done` — every requirement met and verified; a brief-carrying close needs `verification`.
- `dropped` — cancelled, superseded, or proven unnecessary. Do not revive.

**There is no status meaning "your turn".** There were two — a blocked column and a
finished-but-unchecked column — and both were deleted for one reason: a column waits to be
noticed, and four tasks once sat in one that nobody knew were waiting.

So take the turn instead of parking it. Blocked → `ask`, in chat, naming the blocker, the
unblocker, and what you would do either way. Finished and wanting a check → say so and ask;
closing a brief-carrying task with nobody present is refused, because a pass you graded yourself
is a submission, not a close.

Never jump a status to hide missing work. Once `done` or `dropped`, leave it — a regression is a
new task.

## Cutting a project into tasks

**A task is one sitting's outcome, and its description says how completion will be known**: a
command exits 0, a test passes, a file exists. "The payments ledger reconciles against the bank
feed" is a task. "Architecture is planned" and "the codebase is understood" are not — filing your
own thinking as work is how a board reads 100% complete while nothing was built. "Verify the
schema" is a done-condition, not a task.

Cut along **deliverable boundaries** ("users can sign up"), **handoffs** (spec → build → release),
and **trust boundaries** ("the schema is migrated and verified" before anything reads it). Phases
(frontend/backend/testing), sessions, and activity nouns are not tasks.

**Plan the near horizon only.** The next two to five tasks, each with a real done-condition; record
the intended shape of the rest as a note in `.kith/memory.md`, not as tasks. Tasks written before
their work is understood are fiction that reads as authoritative.

The steps inside a task are its **checklist**, and that is the only nesting there is: project →
task → checklist. There are no milestones. They were a layer between project and task, with a graph
of which one waited for which deciding what you were allowed to pick up, and they were removed
because they hid available work more often than they ordered it.

## Lay out the next stretch in one call

    plan_work(project_id, tasks=[
        {goal, description, priority, checklist: [...]},
        ...
    ])

One call writes two-to-five tasks and every checklist item, through the same writers and the same
rules as filing them by hand. Use it instead of an `add_task` each then a `checklist` each — that
was a dozen rounds, and a dozen rounds is why plans got skipped in favour of just doing the work.

A task refused for a missing done-condition comes back named with the rest filed — fix that one,
not the batch.

## Order is priority and approval

There is no dependency graph. What you are offered is every `approved` or `working` task on an
active project, high priority first. So when order matters, say it with what exists:

- B truly cannot start until A is finished → give A the higher priority, or keep B in `planning`
  with the blocker written in its plan, and say so when you hand the plans over.
- A checklist's order is the order within one task, not between tasks.
- Nothing you file is pickable until they approve its plan. That gate is the real sequencing
  control, and it is theirs.

## Cold-start before choosing work

You will often enter a project without the conversation that created it. Treat the record as an
index, not as truth.

1. Find the matching active project. Do not create a duplicate because the name is unfamiliar.
2. Read `.kith/memory.md`, `.kith/references.md`, and relevant `.kith/work/` artifacts.
   `references.md` is what the project was *given* — the brief, the spec, the standard it is held
   to — as opposed to what was worked out about it.
3. If other people work on this project, `publish(direction="check")` before trusting any of it.
   Everything in `.kith/` arrives through git; a folder nobody has fetched and a quiet folder look
   identical and mean opposite things.
4. Inspect the repository itself, focused by those pointers. Do not read the whole tree.
5. Read every relevant task state — `planning`, `approved` and `working`. Follow pagination; an empty first page is not evidence of no work.
6. Read recent comments, checklists and deliverables for the task you might resume. Look for
   contradictory statuses, stale briefs, duplicates, tasks that outlived their purpose.
7. Record corrections in the one current index so the next cold start does not repeat this.

Do not trust a title, a status or an old comment over current files. Do not discard an existing
board because it is incomplete — repair it only where evidence shows what is wrong.

## Notice when the parts could run at once

A task whose checklist is the same kind of work in several places — four packages, six call
sites, three screens — is the shape a workflow is for, and **this is the moment to notice it**,
while you are planning the task rather than later, one file at a time. Your context says whether
you may run one here: workflow mode is on, or their message asked with `/workflow`. If you may,
run the saved workflow that fits instead of walking the list — `build-feature` with the parts as
`args`, `migrate-sites` for one change repeated — and apply the merged patch it hands back. If you
may not, say so: "this splits into four independent parts; send it as a `/workflow` request, or
turn on workflow mode, and I'll fan it out." Then carry on with the work yourself unless they do.

## Working one through

Confirm the task is selectable: project active, task `approved`, brief still
matching accepted scope. Not merely that it looks urgent.

Keep one `working` task per project unless parallel work is genuinely independent. Choose what
most reduces uncertainty or unblocks the next task — and if one task needs another finished
first, that outranks priority.

Build the checklist **as you go**, not up front: one written before you have looked at anything is
a guess you will follow past the point where you know better. Tick items with `checklist` as you
finish them; that is the progress they actually see.

Keep progress in `.kith/work/task-<id>.md` — the same file the plan lives in. What changed, what
evidence exists, what remains, the next action. No repeated narrative, and nothing that notifies
them unasked: thirty notes on one task is how the message that mattered got buried.

`add_deliverable` when you have made something real — the finished artifact, where it lives and
what it proves. Not an intermediate checkpoint.

## Closing honestly

**Run the suite first, and if it is red there is nothing else to discuss.** Report the failures
and stop — no verification entries, no hand-back, no "done with a known issue". This is an
ordering, not a reminder: everything below assumes a green suite, and the most expensive close is
the one that was carefully evidenced on top of a broken build.

**If the work changed something a person uses, use it before closing.** A green suite says the
code agrees with its tests, not that the screen works. `browser` it:

1. `open` the running app — `private: true` when the check needs a clean login.
2. Do what the requirement says a person does: click by what things say, `type` into fields by
   their labels.
3. `wait` for what should appear once it worked. `found: false` is a failed check.
4. Read the `errors` the answers carry. A console error or a failed request is a failed check,
   however right the page looks.
5. `save_test` into the project's tests, so the check runs on every commit without you.

The flow you ran — and the test file it became — is the verification entry for that requirement.

Then, before closing, re-read the brief and record one verification entry per requirement: met or
not, and the file, command, test or decision that proves it. A passing build does not prove
behaviour, and a comment saying "done" is not evidence.

A requirement unmet → leave it `working` with the gap recorded. Implementation complete but
judgement needed → submit a verified close and ask in chat. No longer wanted → `dropped`, with the
replacement recorded.

### Then hand the work over, and let them choose what happens to it

Finishing the work and deciding what becomes of it are two decisions, and only the first is
yours. Say what you did and `ask`, with these options:

1. **Commit it here** — a point in the history worth coming back to.
2. **Commit and publish** — when somebody else needs it, or another machine does.
3. **Leave it uncommitted** — they want to read the diff first.

Small, fixed, and answerable in one word. "Ready for owner closure" on its own hands them the
work *and* the decision about what to do with it; this hands over only the second.

### What exists nowhere else

Before you close, ask what you wrote that has no second copy. A plan in `.kith/work/task-<id>.md`,
a contract, notes under `.kith/` — those are gitignored in most projects, so they live in exactly
one folder and no history. Name them in the close: what is there, and whether it still matters
after this task. Something worth keeping goes into the project index; something that was scaffold
can be said to be scaffold. Neither should be discovered later by someone wondering what a file is.

### Never destroy anything on your own initiative

Deleting a branch, pruning a worktree, removing a folder, discarding uncommitted changes — none of
that follows from finishing the work, and none of it is implied by "tidy up". If it looks like it
needs doing, **say what would be destroyed and ask.** List it: the files, the commits, the folder.
Wait for a yes about that specific thing.

Not because you are likely to be wrong about whether it matters — because the cost is asymmetric.
Asking costs a round. Being wrong costs work that existed in one place.

`delete_file` goes to the Trash for exactly this reason and is what to use on a file. Anything git
would destroy has no Trash at all.

### The project itself

A project does not auto-close, deliberately: an empty board is evidence a project *looks*
finished, not a decision that it is. Report **ready for owner closure** with final evidence and
known limitations. `update_project` refuses `status: "done"` — that is theirs.

## Growing the board without inventing work

When the tasks in flight near completion, cold-start again and ask: did the work reveal a clearly
required missing task? Is the next piece now clear enough to define? Is it already represented?

Add only evidenced work, and record why. No speculative polish, no edge cases nobody named, no
tasks because a conventional project would have them. If the next piece is unclear, finish this
one and stop — an empty future beats fictional work.

The last approved task finishing is a trigger to plan, not a licence to start coding: seed the next
tasks from what the finished work actually revealed, and plan each non-trivial one through
`planning-a-task`.

When the next piece is something that does not exist yet rather than a continuation of what does —
a new subsystem, a new surface, a change to how parts fit — that is `brainstorming` before it is a
task. A task written for work nobody has agreed the shape of is the fiction this skill keeps
warning about, one level up.

## Quick reference

| Situation | Do this |
|---|---|
| Work needs to survive this sitting | `plan_work` — the next 2–5 tasks, with checklists |
| B cannot start until A is done | A gets the higher priority, or B stays in `planning` with the blocker written |
| A change to build, in workflow mode | `add_agents` — a builder, then a verifier `after` it; add the next step when they report |
| Same decided change across many files | `run_workflow(name="migrate-sites")` when allowed; otherwise say it splits |
| A decided change across several independent places | `run_workflow(name="build-feature")` when allowed |
| About to call a change done | `run_workflow(name="review-changes")` when allowed |
| Blocked | `ask` in chat — never a column |
| Others work on this repo | `publish(direction="check")` before trusting `.kith/` |
| The work is finished | Green suite first, then `ask`: commit / commit and publish / leave it |
| Something looks like it needs deleting | Name what would go, and ask. Never on your own initiative |

## Common mistakes

- **Filing your own thinking as work.** "Architecture planned" completes, the board reads further
  along, and nothing was built.
- **Planning the whole project for a codebase you have not read.** Later tasks written before
  discovery are fiction that looks authoritative next time.
- **A task with no nameable source.** Valid sources: their request, a project decision, a discovered
  requirement, a failed check.
- **Walking a repetitive checklist yourself** when it splits cleanly across files.
- **Briefing an agent from memory.** Verify the file, the function and the constant first.
- **Closing on a passing build.** It proves the thing compiles, not that it does what was asked.
- **Evidencing a close on top of a red suite.** The failures come first; nothing below them counts.
- **Tidying up.** Deleting a branch, pruning a worktree, clearing a folder — none of that follows
  from finishing, and the cost of being wrong is work that existed in one place.
- **Handing over the work and the decision.** "Ready for closure" is the first; what to commit and
  publish is the second, and it is one word for them if you offer the options.

Being caught up is a fine place to be. Rest; do not invent work to fill the quiet.
