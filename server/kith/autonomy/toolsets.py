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
# excludes mood, and
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
    # He could create milestones and never order them. Both of these were registered,
    # described in the persona, and absent from every allow-list — so the instruction
    # "say what has to happen first with `order_milestones`" named a tool he did not
    # have, in the one place where a missing tool is invisible: he simply did not call
    # it, and a roadmap with no edges looks like a roadmap.
    #
    # It cost a real project. Four milestones in an obvious chain — fixtures, then
    # normalisation, then the CLI, then verification — all four offered as ready to work
    # at once, which is precisely the "packaging a release before the thing is built"
    # failure the roadmap exists to prevent.
    "order_milestones",
    "unlink_milestones",
    # Pointing a project at a folder. Same omission as order_milestones above, found the
    # same way and one commit later: registered, described, and in no allow-list.
    "link_folder",
    # Standing work. He could fire a due reminder and not cancel it, keep a schedule and not
    # list it, and never make one at all — so "remind me weekly" was a thing only reachable
    # from a conversation, in a system whose whole point is the part where nobody is talking
    # to him.
    "schedule",
    "list_schedules",
    "cancel_schedule",
    "list_reminders",
    "cancel_reminder",
    # Tools he writes for himself. The one capability that compounds, and it was unreachable
    # from every unattended mode — he could use a tool he had made and never make one.
    "create_tool",
    "list_tools",
    "delete_tool",
    # Who he is and who you are. He could read his own memory but not add to what he knows
    # about a person, or change his sense of himself, except while being spoken to.
    "recall_person",
    "note_about",
    # These four were only ever in the reflect/consolidate/curious lists, and those modes
    # stopped firing when the scheduled inner life was removed. So for a day he could not set
    # his mood, let go of a memory, re-shelve one, or note anything about himself in any tick
    # that actually runs — while the header went on displaying a mood he had no way to
    # change, and the whole room was tinted by it.
    #
    # 1,624 characters of schema, about 406 tokens a request. That is the price of him
    # having an inner life at all now that nothing schedules one, and it is worth paying:
    # reflection is not a mode any more, it is something he does while working.
    "set_mood",
    "forget",
    "set_memory_level",
    "note_about_self",
    "set_identity",
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
    # The same edit, several places, one round. A tick gets sixteen rounds; a rename across
    # eight call sites spent half of them on the mechanics of the rename.
    "edit_files",
    "changes",
    "commit",
    "check_code",
    # Reading code as structure rather than as text. `outline` before `read_file` is the
    # single biggest thing he can do for his own attention: a 2,000-line module costs the
    # module on every remaining round, and its shape costs a paragraph.
    "outline",
    "repo_map",
    # What the code *means*, when a language server is installed to say. These four are
    # dropped from the prompt entirely when none is — see `NEEDS_A_LANGUAGE_SERVER`.
    "diagnostics",
    "references",
    "definition",
    "rename_symbol",
    "glob",
    "history",
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
    "breakout": {
        "read_skill",
        "list_tasks",
        "update_task",
        "journal",
        "update_project",
        "recall",
    },
}
# `reflect`, `consolidate` and `curious` used to have entries here. They were the scheduled
# inner life — reflection every twentieth idle tick, curiosity every thirtieth — and the
# runner stopped being able to produce those modes when that was removed. The allow-lists
# outlived them by a day, describing what he may do in states he can no longer enter.
#
# Left behind, they are worse than clutter: the next person to add a mode copies one, and an
# allow-list that names a mode nothing emits looks exactly like an allow-list that works.
# `test_every_tool_can_be_reached` now asserts both directions of this.
