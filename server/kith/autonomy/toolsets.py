"""Which tools each kind of tick may use.

Scoping rather than handing him everything, for two reasons that pull the same way. The
per-round prompt carries every tool schema he is offered, so an unscoped tick pays for
23k characters of things it will not call; and a model given fifty options behaves worse
at the one it should be reaching for than a model given twenty.

Split out of runner.py because it is a list, not logic. It changes when his capabilities
change, which is a different reason and a different rhythm from the loop that consumes it.
"""

from __future__ import annotations

# Everything needed to actually execute a task: tasks/projects, memory & notes,
# knowledge (sources + web), the sandbox, and deferring/handing back. Deliberately
# excludes identity/mood/people-mgmt, curiosity, schedule/reminder management, and
# the heavy tool-builder schemas — none of which move a work task forward, and all
# of which bloat the per-round prompt.
_WORK = {
    "list_tasks",
    "view_task",
    "add_task",
    "update_task",
    "comment_on_task",
    "ask_on_task",
    "add_checklist_item",
    "check_item",
    "add_deliverable",
    "create_project",
    "list_projects",
    "update_project",
    "add_milestone",
    "update_milestone",
    "recall",
    "remember",
    "take_note",
    "read_notes",
    "update_note",
    "journal",
    "read_journal",
    "search_sources",
    "read_source",
    "web_search",
    "fetch_url",
    "browse_page",
    "shell",
    "read_file",
    "grep",
    "write_file",
    "edit_file",
    # Without this he has `shell` and nothing else for "get rid of that file", which means
    # `rm` — the one delete on the machine with no way back — during a tick nobody is
    # watching. `delete_file` uses the Trash and goes through the permission check, and it
    # was reachable from chat but not from here, which is exactly backwards: the unattended
    # case is the one that most needs the recoverable route.
    "delete_file",
    "list_files",
    "set_reminder",
    "reach_out",
    # A skill can apply to any kind of work, and it is one cheap call to find out.
    "read_skill",
}

# Which tools each tick-mode actually needs — scoping keeps the prompt lean and
# the model focused. Action modes (work/due → "start", and "reply") get the work
# toolset; the reflective modes get a smaller relevant subset.
_ALLOW: dict[str, set[str]] = {
    "start": _WORK,
    "reply": _WORK,
    "reflect": {
        "read_skill",
        "read_journal",
        "journal",
        "list_tasks",
        "update_task",
        "recall",
        "remember",
        "set_memory_level",
        "set_mood",
        "note_about_self",
        "wonder",
        "update_curiosity",
        "reach_out",
    },
    "consolidate": {
        "read_skill",
        "read_journal",
        "list_tasks",
        "recall",
        "remember",
        "forget",
        "set_memory_level",
        "journal",
    },
    "breakout": {
        "read_skill",
        "list_tasks",
        "update_task",
        "journal",
        "wonder",
        "update_curiosity",
        "update_project",
        "recall",
    },
    "curious": {
        "read_skill",
        "wonder",
        "update_curiosity",
        "web_search",
        "fetch_url",
        "search_sources",
        "remember",
        "journal",
        "reach_out",
        "read_file",
        "write_file",
        "shell",
        "view_task",
    },
}
