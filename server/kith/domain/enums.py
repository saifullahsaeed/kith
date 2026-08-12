"""The vocabulary of Kith's world — the fixed sets a value may take.

Kept out of the database layer on purpose: these are facts about the domain,
not about how it happens to be stored, and both the API and the tool schemas
need them without wanting a database import."""

from __future__ import annotations

MEMORY_LEVELS = ("core", "recall")
REMINDER_STATUSES = ("pending", "done", "cancelled")
CURIOSITY_STATUSES = ("open", "exploring", "explored", "dropped")
SCHEDULE_STATUSES = ("active", "paused")
# The states a task can be in. Four, and a person sets none of them.
#
#   planning  a plan is being drafted
#   approved  you said yes; he may work it
#   working   he is on it
#   done
#   dropped
#
# There were eight, and the board was not using them: counted the day this changed, 67 done,
# 4 waiting, 3 planned, 3 dropped, 3 backlog, 1 working, and **nothing had ever been in
# `review`**. Two were dead and two more were indistinguishable in practice, which is how a
# status comes to be wrong often enough that removing it beats fixing it.
#
# 'backlog' is gone because every task now lands in the gate, so there is no state before
# planning. 'planned' is 'approved', which is what it always meant.
#
# 'review' and 'waiting' are gone, and their absence is the point rather than a simplification.
# Both meant "someone else's turn", and a column is a bad way to take a turn: it waits to be
# noticed. `ask` is the good way — it holds the turn until it is answered, in the conversation
# the work came out of. So finished work and blocked work both reach a person as a question, and
# the task stays `working` until they answer. `review`'s real job — that he cannot be the only
# judge of his own work — is done better by being asked than by being queued.
#
# The gate between 'planning' and 'approved' is unchanged and is the only decision a person
# makes about a task: a plan nobody but its author has seen is a guess wearing the clothes of a
# decision. It is entered from chat, never on his own initiative, and `update_task` refuses it
# without a plan and a checklist to approve.
TASK_STATUSES = ("planning", "approved", "working", "done", "dropped")
#: What he may pick up without being asked for it by name. `planning` is not here: nothing is
#: pickable until a person has approved a plan for it.
TASK_ACTIVE = ("approved", "working")
#: Closed, either way. The one set both `active_tasks` (repositories/tasks.py) and `add_task`'s
#: duplicate-merge / milestone-cap checks (tools/tasks.py) need, kept in one place after both
#: had grown their own copy of the same two strings.
TASK_SETTLED = ("done", "dropped")
TASK_PRIORITIES = ("low", "normal", "high")
PROJECT_STATUSES = ("active", "done", "paused", "archived")
MILESTONE_STATUSES = ("todo", "done")
