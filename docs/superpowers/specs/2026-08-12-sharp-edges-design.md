# The sharp edges

**Date:** 2026-08-12
**Status:** approved, in build

Four contained defects found by reading every conversation and log from 2026-08-10 to
2026-08-12. They share nothing architecturally — they are grouped because each is small,
each was felt, and none of them needs the context rebuild that tranches 2 and 3 do.

The measurements behind them, over that window: 158 turns, 2,723 model rounds, $39.11,
2,840 tool calls of which 585 (21%) were byte-identical repeats and 121 (4.3%) failed.

## a. `already satisfied` is not `cannot apply`

**What happened.** `edit_file` failed 96 times out of 467 calls — a 21% failure rate on the
tool he uses most after `shell`. Seventy-two of those were the same message: *old and new are
identical, so there is nothing to change*. In batches it was worse: one already-applied edit
inside an `edit_files` call discarded every other edit in the batch, including a ten-edit one.

**Why.** `_apply_edit` has two outcomes, applied and refused, and `old == new` was sorted into
refused. But those 72 calls were not mistakes about the file — they were mistakes about
*time*. He had already made that change earlier in the turn and lost track of it, which is
what a model does when its working memory is a 460,000-token transcript. The file was already
in the state he asked for.

**What it becomes.** A third outcome: **satisfied**. No bytes to change because the file
already reads that way.

* `edit_file` returns successfully, and the report says plainly that nothing changed and why.
* `edit_files` counts a satisfied edit as passing, so the other nine in the batch land.
* Atomicity is untouched. An edit that genuinely cannot apply — text not found, ambiguous
  match — still discards the whole batch.

**The principle this appears to violate, and why it doesn't.** `edit_file` was built to refuse
rather than guess, because "a silent no-op reads as success and he moves on believing the
change landed". That reasoning is about *not-found*, where the requested state was **not**
reached and silence is a lie. Satisfied is the opposite case: the requested state is already
true, so success is the honest answer. The report still says out loud that no bytes moved, so
it is a loud no-op, never a silent one — which is what the original decision was protecting.

## b. One display path, not one per call site

**What happened.** `glob` failed 14 times out of 23 — a 61% failure rate — handing the model a
raw Python `ValueError: '…/ai-play/…' is not in the subpath of '/Users/saifullahsaeed/Kith'`.

**Why.** `permissions.require_path` correctly *allows* a folder linked to an active project,
so the search runs; then `files.glob` formats its results with `relative_to(root())`, which
assumes every result lives under his own folder. The permission layer knows about linked
projects and the formatting layer does not.

**What it becomes.** A `paths.display(path)` helper, and `glob` uses it:

* relative to `root()` when the path is inside it,
* relative to the nearest linked project root when it is inside one of those,
* absolute otherwise.

It is a helper rather than a patched line because the bug is a *class* — any code that
formats a path for him has the same assumption available to make. `files.glob:705` is the only
current site that can see a path outside root; `persona.py`'s uses are base-relative by
construction and stay as they are.

## c. The plan travels with the task

**What happened.** *"where the fuck is plan on task do you not attach plan on tasks"*,
2026-08-10 12:00 — four days after the planning gate shipped.

**Why.** `_plan_doc` reads `.kith/work/task-<id>.md` exactly once: when a status moves into
`planning`, so the approval carries the doc. `task_detail` returns comments, checklist and
deliverables and never the plan, so the drawer — the place you go to read a task — is the one
place the plan is absent. It also requires a `project_id`, so a standalone task can never
carry a plan at all, even though every task now goes through the same gate.

**What it becomes.** `task_detail` returns the plan alongside the other three collections, and
`_plan_doc` resolves a task with no project against `~/Kith/.kith/work/task-<id>.md`. The
drawer renders it as a section.

The file on disk stays the single source of truth — the plan is read at request time and never
copied into the database, so editing the file is still how you edit the plan.

## d. The permission ask that denies itself

**What happened.** Two tests fail on the current tree:

```
tests/test_permissions.py::TestWaitingForYourAnswer::test_allowing_lets_the_waiting_call_through
  assert outcome == [], "it decided without waiting to be asked"   →   ['denied']
tests/test_notify.py::TestAnnouncing::test_a_failing_notification_never_breaks_the_caller
  assert notify.announce("asked", "come look") is False            →   True
```

A permission request resolves to `denied` without waiting to be asked.

**The hypothesis.** `permission_mode` is set to `bypass` in the live config — every guard in
`permissions.py` off, on a machine with real shell access and no container. The transcripts
show *"Not allowed yet: he wants to write to something outside his workspace… ask them to
allow it"* arriving as a hard tool failure rather than as a prompt anyone could answer. If
asking cannot be answered, turning the asking off is the rational move, and the safety layer
is disabled as a symptom rather than as a preference.

**What happens next.** This one is debugged before it is designed. Reproduce the failing wait,
find why the outcome resolves without a question being posted, fix that — and only then decide
whether `bypass` can come off. The notify failure is checked separately and may well be the
test's fault injection not taking on real macOS rather than a live defect; if either turns out
to be a test artifact, that gets said plainly rather than fixed into a pass.

## Out of scope

Everything that touches the model's context, the loop, or routing. Named here so the boundary
is explicit, each to be specified on its own when we reach it:

* **Tranche 2 — routing.** `prefer_provider_by` defaults to `price`, which steered a turn
  through Baidu → Decart → Baidu → CoreWeave → Alibaba, resetting the prompt cache on every
  hop; a host that returns reasoning inside `content` had its raw chain-of-thought streamed to
  the user as the reply, once as degenerate word-salad; a round that hits the 8,192-token
  output cap is treated as a reply rather than as truncation.
* **Tranche 3 — the turn's working memory.** Median prompt of 459,615 tokens per round, 55% of
  rounds under 15 tokens/second, 163 silences over a minute with nothing shown, 59% of reads
  being re-reads, `check_process` polled by burning a full round per poll, and a forced
  hand-back at round 80 that turns "finish this" into "here is what is pending".

## Verification

`./check` — ruff, ruff format, pytest, tsc, vite build — green before any of this counts as
done, with the two failures above accounted for rather than skipped. Each of (a), (b), (c)
lands with tests written first; (d) lands with the reproduction that proves the diagnosis.
