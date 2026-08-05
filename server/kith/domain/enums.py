"""The vocabulary of Kith's world — the fixed sets a value may take.

Kept out of the database layer on purpose: these are facts about the domain,
not about how it happens to be stored, and both the API and the tool schemas
need them without wanting a database import."""

from __future__ import annotations

MEMORY_LEVELS = ("core", "recall")
REMINDER_STATUSES = ("pending", "done", "cancelled")
CURIOSITY_STATUSES = ("open", "exploring", "explored", "dropped")
SCHEDULE_STATUSES = ("active", "paused")
# Kanban columns for tasks. 'todo'/'doing' are actionable; 'backlog' is not yet
# started, 'review' is finished work nobody has checked yet, 'waiting' is parked on
# the person, 'done'/'dropped' are closed.
#
# 'review' exists because an unattended tick was the only judge of its own work. It ran
# `_verify_done` on itself — enumerate the brief, answer per item — and then closed the task,
# which is marking your own homework with the answer sheet you wrote. It is also the column that
# stops a task being reopened and ground on: nothing in `review` is offered to a tick at all, so
# work that is finished-pending-a-look cannot be picked up and reworked for another twelve hours.
TASK_STATUSES = ("backlog", "todo", "doing", "review", "waiting", "done", "dropped")
#: What a tick may pick up. Deliberately narrow — `review` and `waiting` are both "someone else's
#: turn", and a tick that could reach either would take back work it had already handed over.
TASK_ACTIVE = ("todo", "doing")
TASK_PRIORITIES = ("low", "normal", "high")
PROJECT_STATUSES = ("active", "done", "paused", "archived")
MILESTONE_STATUSES = ("todo", "done")
