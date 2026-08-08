"""The vocabulary of Kith's world — the fixed sets a value may take.

Kept out of the database layer on purpose: these are facts about the domain,
not about how it happens to be stored, and both the API and the tool schemas
need them without wanting a database import."""

from __future__ import annotations

MEMORY_LEVELS = ("core", "recall")
REMINDER_STATUSES = ("pending", "done", "cancelled")
CURIOSITY_STATUSES = ("open", "exploring", "explored", "dropped")
SCHEDULE_STATUSES = ("active", "paused")
# Kanban columns for tasks. 'backlog' is not yet started; 'planning' is a plan being
# drafted or awaiting approval; 'planned' is approved and ready to implement; 'working' is
# actionable, in progress; 'review' is finished work nobody has checked yet; 'waiting' is
# parked on the person; 'done'/'dropped' are closed.
#
# The gate between 'planning' and 'planned' exists for the same reason the one below does,
# one step earlier: a plan nobody but its author has seen is a guess wearing the clothes of a
# decision. Entering 'planning' is always a person asking for one, in chat — never his own
# initiative — so a plan waiting for a look is always one somebody actually wanted looked at.
#
# 'review' exists because he was the only judge of his own work. It ran
# `_verify_done` on itself — enumerate the brief, answer per item — and then closed the task,
# which is marking your own homework with the answer sheet you wrote. It is also the column that
# stops a task being reopened and ground on: nothing in `review` is offered to him at all, so
# work that is finished-pending-a-look cannot be picked up and reworked for another twelve hours.
TASK_STATUSES = ("backlog", "planning", "planned", "working", "review", "waiting", "done", "dropped")
#: What he may pick up without being asked for it by name. Deliberately narrow — `backlog` and `planning` are not
#: here because starting either is always a person's decision, made in chat, not his; and
#: `review`/`waiting` are both "someone else's turn", so reaching either would
#: take back work it had already handed over.
TASK_ACTIVE = ("planned", "working")
#: Closed, either way. The one set both `active_tasks` (repositories/tasks.py) and `add_task`'s
#: duplicate-merge / milestone-cap checks (tools/tasks.py) need, kept in one place after both
#: had grown their own copy of the same two strings.
TASK_SETTLED = ("done", "dropped")
TASK_PRIORITIES = ("low", "normal", "high")
PROJECT_STATUSES = ("active", "done", "paused", "archived")
MILESTONE_STATUSES = ("todo", "done")
