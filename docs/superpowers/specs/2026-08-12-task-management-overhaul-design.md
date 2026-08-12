# The board is his working memory, not your inbox

**Date:** 2026-08-12
**Status:** approved, in build

## The problem, in his own numbers

The task system asks you for things it should decide, and tells you things it should keep to
itself. Counted on the live database:

* **620 task comments.** Every one raised a notification. The Alerts panel is a wall of "New note
  on 'Restore CI compatibility…' (task #79): Checked remote status…", four of them inside twenty
  minutes on one task.
* **77 of 81 tasks carry a due date**, essentially all of them auto-stamped. A field that is
  always set carries no signal.
* **Eight statuses, and the board does not use them.** 67 done, 4 waiting, 3 planned, 3 dropped,
  3 backlog, 1 working — **nothing has ever been in `review`**. Two of the eight are unused and
  two more are indistinguishable in practice.
* **Nine projects, six of them on `…/Desktop/Personal/ai-play`, two of those `active`** — one
  named `placeholder`, one named "Odoo Accounting plugin pilot (duplicate board entry)", and two
  more sharing the name "Odoo Accounting Agent". The rule against this was already written in the
  skill and nothing enforced it.
* **24 tasks have no checklist at all**, including ones that were approved and worked.

The through-line: you are asked to maintain state he should own, and interrupted with progress
you did not ask for, while the one decision that is genuinely yours — *is this plan right* —
arrives without the plan attached.

**So: you make exactly one decision per task, approving its plan. Everything else he decides,
and anything he needs from you arrives as a question in chat.**

## 1. What is waiting on you, and what he is working on, both move into the Work panel

Two blocks at the top of the Work panel, above the activity feed:

**Waiting on you** — assembled from *live state*, never from the message log:

| Source | Shows |
| --- | --- |
| `questions.open_question(conversation)` | a question he is parked on |
| `permissions.pending()` | a permission he is parked on |
| `toApprove` | plans awaiting approval |

**Working on** — the existing `WorkingOn` strip, moved out of the composer area
(`thread.tsx:213`) and into the panel: the task, its live *n/m* count, and the checklist ticking
as he goes.

Alerts keeps history only — notes, finished, reached out — and loses its "Waiting on you" tab and
the `wants: true` flags that fed it.

**Why live state and not messages.** The permission card in today's Alerts —
*"I need your say-so before I can read …/ai-play/.kith/work/task-99.md"* — cannot be actioned:
`permission_mode` is `bypass`, so that read is already allowed, and the row is a leftover from
when it wasn't. A panel that reads current state cannot go stale, and a message log always will.

## 2. Four statuses, and you never set one

```
planning  → he is drafting a plan
approved  → you said yes; he may work it
working   → he is on it
done
dropped
```

`TASK_ACTIVE = (approved, working)` — nothing is pickable before you approve.

`review` and `waiting` are gone. Finished work and blocked work both reach you as a **question in
chat**, which is where the context is: chat has the conversation the work came out of, and `ask`
already holds the turn until you answer. Nothing is in `review` today and four things are in
`waiting`, so this costs almost nothing to migrate.

Migration **v34**: `backlog→planning`, `planned→approved`, `doing→working`, `waiting→working`,
`review→working`; `done` and `dropped` unchanged. `review→working` rather than `→done`, because
work he claimed was finished and nobody checked is not finished, and the honest state is that he
is still on it — he will raise it in chat.

In the interface a status is a **plain label**. No dropdown on the card, none in the drawer, no
move-between-columns control. The Kanban board stays as four read-only columns, because seeing
where work sits is useful; only setting it goes. `priority` is untouched.

## 3. Dates are gone

`due_at` comes off the `add_task` and `update_task` schemas, out of the task drawer, and out of
the table in the same migration.

## 4. Task comments are gone

Removed: the `comment_on_task` and `ask_on_task` tools, the comment thread and reply box in the
drawer, `comments` from `task_detail`, `tasks_awaiting_kith` and the resume tick it fed, and the
notifications every comment raised.

Each thing they were used for already has a better home:

| Was | Becomes |
| --- | --- |
| narrating progress on a task | the plan file `.kith/work/task-<id>.md`, now visible on the task |
| "I need an answer to continue" | `ask`, in chat, which holds the turn |
| evidence that it is done | the checklist and the deliverables |

**The 620 existing rows are not deleted.** The feature, the tool and the surface go; the table
stays in place and unread, because destroying history is not what "get rid of that feature"
asked for. Dropping it later is one migration.

## 5. One project per chat, enforced in code rather than in prose

`running-a-project` already says "a conversation locks to the first project it works on and stays
there for the rest of its life". It is a paragraph in a skill, and the board above is what a
paragraph achieves.

* `create_project` **refuses** when the conversation is already on a project, and names it.
* `create_project` **refuses** when a project already exists for that directory, returning the
  existing one instead of a seventh.
* Writing to a task, checklist, deliverable or milestone **outside the conversation's project**
  refuses and says why: *"this conversation is Sadeef AI; task #57 belongs to Sadeef Client
  Portal — that needs its own chat."*
* **Reads stay open.** Looking at another project is fine and often necessary.

Existing rows are left exactly as they are, by your call. This stops it happening again; it does
not tidy up.

## 6. Approval requires a plan and the whole checklist

`approved` is unreachable unless **both**:

1. a plan exists at `.kith/work/task-<id>.md`, and
2. the task has at least one checklist item.

Enforced in `update_task`, not in the skill, so it cannot be forgotten — and the refusal says
which of the two is missing. The approval surface shows the plan and the checklist together, so
what you are approving is in front of you.

The 24 tasks with no checklist become un-approvable until one is written. That is the intent, not
a side effect.

## 7. Both skills rewritten

`running-a-project` (330 lines) and `planning-a-task` (106) rewritten to this vocabulary — four
statuses, no comments, no dates, one project per chat, plan-and-checklist before approval — and
shorter, because most of what they currently spend words on is now enforced by the code.

## Build order

Each finished end to end — server, interface, tests, `./check` green — before the next starts.

1. Four statuses, dates removed, migration v34
2. Comments removed, asking moved to chat
3. The approval gate
4. One project per chat
5. Waiting-on-you and Working-on in the Work panel
6. The two skills

## Out of scope

* Tidying the nine existing projects. Asked and declined: enforce going forward only.
* Deleting the 620 comment rows.
* Reminders. The *"One of your reminders just fired"* turns are a related source of noise in the
  same transcripts, but they are not what was asked for here.
