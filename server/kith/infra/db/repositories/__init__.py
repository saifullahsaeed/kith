"""One module per entity. Each owns its tables and nothing else.

Imported here so a consumer needs a single import and then says which entity it is
touching — ``repo.tasks.add_task(...)`` rather than a flat namespace of 98 functions
where nothing tells you what a call will read or write.
"""

from kith.infra.db.repositories import (
    activity,
    conversations,
    custom_tools,
    journal,
    memories,
    messages,
    notes,
    people,
    projects,
    reminders,
    schedules,
    self_model,
    sources,
    tasks,
)

__all__ = [
    "activity",
    "conversations",
    "custom_tools",
    "journal",
    "memories",
    "messages",
    "notes",
    "people",
    "projects",
    "reminders",
    "schedules",
    "self_model",
    "sources",
    "tasks",
]
