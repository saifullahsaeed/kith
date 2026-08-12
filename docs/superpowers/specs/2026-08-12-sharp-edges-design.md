# The sharp edges

**Date:** 2026-08-12
**Status:** done — `40deab2`. 2117 passed. (d) turned out differently from the hypothesis; see it.

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

**The hypothesis was that asking is broken.** `permission_mode` is set to `bypass` in the live
config — every guard off, on a machine with real shell access and no container — and the
transcripts show *"Not allowed yet: he wants to write to something outside his workspace… ask
them to allow it"* arriving as a hard tool failure. If asking cannot be answered, turning the
asking off is the rational move.

**The evidence does not support it.** Both real denials are explained without a live defect:

* 2026-08-10 04:41 (`try creating a folder test` / `try on desktop`) predates `0f1e7d6` at
  06:03, which is the commit that made a refusal *wait* at all. Refusing immediately was the
  behaviour at the time, not a bug in it.
* 2026-08-11 01:26 (pushing to a remote) was a reminder-driven turn. A conversation with no
  live turn refuses at once **on purpose** — waiting would park a thread for fifteen minutes on
  a prompt drawn on nobody's screen.

So `bypass` is not evidence that the gate is broken, and nothing here justifies taking it off.
That is the user's call and it stays theirs.

**What the two failures actually were: stale assertions, not bugs.** Both belong to `ba6ec1b`
(08-10 08:58), whose own subject says "with an unresolved test regression" and whose body lists
`TestWaitingForYourAnswer` as still to do. A conversation id alone no longer blocks — it takes a
live turn — and `announce` now promises "sent" rather than "arrived", because delivery moved off
the caller's thread so a 55-second desktop timeout cannot hold a turn that is already parked.
Rewritten to assert what is true now, plus a test each for the deliberate behaviour introduced.

**One real defect found on the way.** That commit says the notification *moved* below the "is
anybody watching" guard. The diff added a call below it and left the original above, so the move
was an add: an attended refusal raised two alerts for one thing to approve, and an unattended one
still raised the alert the guard exists to suppress. Fixed, with a test per case.

**And two tests that were passing on luck.** `test_a_long_body_is_trimmed…` and
`test_the_link_reaches_the_notification` read values written by the notify thread immediately
after `announce` returns. Demonstrated rather than assumed: 50ms of delay in the notifier breaks
that shape with `KeyError: 'body'`. They wait for delivery now.

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
