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
# started, 'waiting' is parked on the person, 'done'/'dropped' are closed.
TASK_STATUSES = ("backlog", "todo", "doing", "waiting", "done", "dropped")
TASK_ACTIVE = ("todo", "doing")
TASK_PRIORITIES = ("low", "normal", "high")
PROJECT_STATUSES = ("active", "done", "paused", "archived")
MILESTONE_STATUSES = ("todo", "done")
