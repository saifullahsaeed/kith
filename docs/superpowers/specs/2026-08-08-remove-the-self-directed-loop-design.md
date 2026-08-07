# Remove the self-directed loop

**Date:** 2026-08-08
**Status:** approved, not yet implemented

Autonomy — Kith picking his own task and working it unattended — is being removed. The
premise did not work out, and the spec proceeds as though the concept had never existed
rather than leaving it switched off.

Two things ride along inside the same module and are **not** part of that idea. Both stay.

## What `autonomy/runner.py` actually is

One 1,291-line class doing three separable jobs. Only the first is leaving.

**1. The self-directed loop.** A background thread that round-robins over sessions, decides
what is worth doing, takes a step, and watches for grinding and stalling: `_loop`, `_step`,
`_tick`, `_next_session`, `_sessions`, `_in_scope`, `_why_idle`, `_new_pending`,
`_sessions_working`, `keep_working`, `nudge`, `rest`, `tick_now`, `ensure_loop`,
`_awaiting_review`, `_awaiting_approval`, and the grind/stall/focus counters.

**2. A scheduler.** `_fire_conversation_reminders()` and `_continue_conversation()` — a due
reminder or schedule waking the conversation it belongs to. This has nothing to do with
self-direction; it was called from `_tick` because `_tick` was the only thing running.

**3. An activity feed and cost ledger.** `publish` / `subscribe` / `recent`, `status()`,
`_charge_session()`. **Chat already depends on this.** `_MindFeed` in `api/routes/chat.py`
publishes every chat turn through `runner.publish(...)`. Deleting the runner outright would
take the Mind panel down for chat as well as for ticks.

So this is not a deletion. It is a split, and then a deletion of one of the three parts.

## Target shape

```
kith/services/scheduler.py    due reminders + schedules wake their own chat
kith/services/activity.py     the live feed, session cost accounting, status
kith/autonomy/                deleted
kith/api/routes/autonomy.py   deleted; /activity and /usage rehomed
```

`scheduler.py` owns a timer thread whose only job is "is anything due?". It does not choose
work, does not look at tasks, and has no notion of a session being worth visiting. When
something is due it calls the same `_turn` the chat route calls, bound to that conversation.

`activity.py` owns what the interface reads: the event feed the Mind panel subscribes to, the
per-session cost accounting, and a `status()` reduced to what still means anything.

## The API surface

| Endpoint | Fate |
|---|---|
| `GET /autonomy` | gone |
| `POST /autonomy` | gone |
| `GET /autonomy/stream` | becomes `GET /activity/stream` |
| `GET /activity` | stays, rehomed to a new `routes/activity.py` |
| `GET /usage` | stays, rehomed |

## Naming

`tick_log` is the durable flight recorder, and it is where **chat** turns record what they
cost — `add_tick_log(..., mode="chat", ...)` is called from `_MindFeed.finish()`. The name
goes; the table and every column stay.

- table `tick_log` → `turn_log`, via a migration in `infra/db/migrations.py`
- `add_tick_log` → `add_turn_log`, `list_tick_log` → `list_turn_log`,
  `tick_log_summary` → `turn_log_summary`
- the `mode` column keeps its values, minus `"tick"`

Losing cost tracking here would be the worst possible outcome of this change, so the
migration is the part of the plan that gets tested first.

## What is deliberately kept

**Persona, entirely.** Untouched — no files removed, no loader changed, no settings tab
touched. What *is* removed is tick vocabulary written into the prompt layer, so nothing
instructs him about a mode that no longer exists. The persona files themselves barely mention
it: `00-who.md`'s "on your own" is about having his own judgment, not about running
unattended, and it stays exactly as written.

**Projects, tasks, milestones, the roadmap.** These stop being a queue something drains while
you are away and become a plan the two of you work through in a conversation. Every status
still means something: `waiting` is still "he asked you a question and stopped", `review` is
still "he believes this is done and nobody has checked".

**Reminders and schedules**, per above.

## What is removed from the interface

`hooks/use-autonomy.ts`, `lib/backend/autonomy.ts`, the roaming controls in
`shell/header-controls.tsx` and `chat/session-bar.tsx`, and the autonomy half of
`shell/app-header.tsx` and `shell/presence.tsx`. The Mind panel (`chat/work-panel.tsx`)
stays and reads the rehomed activity feed. The green "working" wash stays, driven by the new
meaning below.

`conversations.working` — the persisted "this session keeps going without being asked" — goes
with the loop. The column is dropped in the same migration.

## The convergence with the reliability work

The follow-on change (reattach, round retry, busy sessions) gets simpler, not harder. "One
notion of busy" was awkward while `working` meant "roams". With roaming gone there is exactly
one meaning available:

> A session is busy if and only if a turn is live in it.

No merge, no second concept, no column.

## Tests

49 of 102 test files reference ticks. They fall into three buckets, and the rule is:

- **Encodes behaviour that is disappearing → delete the file.** `test_stopping_a_step.py`,
  `test_where_a_tick_works.py`, `test_getting_on_with_it.py`, `test_stall_detection.py`,
  `test_idle_reasons.py`, `test_a_task_that_never_progresses_escalates.py`,
  `test_a_session_that_keeps_working.py`, `test_a_tick_gets_room_to_finish.py`,
  `test_work_directive_asks_for_attempt_shape.py` and the rest of that shape.
- **Tests something that survives, via a tick → rewrite it against a chat turn.**
  `test_a_reminder_reports_back_to_its_chat.py`, `test_what_it_cost.py`,
  `test_the_budget_is_money.py`, `test_a_provider_error_backs_off.py`,
  `test_the_last_round_costs_what_the_others_did.py`, `test_a_session_owns_its_project.py`,
  `test_roadmap.py`, `test_tuning.py`, `test_api_auth.py`.
- **Guards a hazard the change does not remove → keep, repointed.**
  `test_the_suite_does_not_start_loops.py` is the whole of this bucket and it is easy to get
  wrong. It reads as tick-specific — it names `keep_working` and `ensure_loop` — but what it
  actually guarantees is that no test leaves a background thread running against the real
  database. `scheduler.py` starts a timer thread, so the hazard survives the loop that
  introduced it. The file stays and is repointed at the scheduler.

Deleting tests was approved explicitly. The risk being accepted is that a behaviour worth
keeping is only covered by a deleted file, so every deletion is checked against the other two
buckets before it happens rather than after. The bucket-three case above is what that check
is for, and it was found by running it.

New coverage this change owes:

- the migration preserves every existing `tick_log` row and its costs
- a due reminder still wakes its own conversation with no loop running
- a due schedule likewise
- the activity feed still carries chat turns

## Tuning knobs

Every knob that only steered the loop is removed from `domain/tuning.py`:
`focus_grind_limit`, `stall_break`, `stall_giveup`, and the tick-only round budget. Knobs
shared with chat — `max_rounds`, `landing_reserve`, `landing_effort`, the money budget — stay.
`test_tuning.py` asserts across the whole `TUNABLES` tuple, so it is rewritten rather than
deleted.

## Order of work

Two commits, in this order, because reviewing them together would be unreasonable:

1. **Remove the loop.** The split, the deletions, the migration, the vocabulary scrub, the
   test triage. Ends green.
2. **The reliability work.** Reattach to a live turn, retry a failed round then land, busy
   sessions. Its own spec.

## Out of scope

- Surviving a server restart mid-turn
- Retrying indefinitely through a long provider outage
- Any change to persona
- Any change to what tasks, projects or the roadmap mean
