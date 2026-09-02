"""Tool names Kith reaches for that do not exist.

Spelling distance cannot fix these: `run_command` and `shell` share no letters, and
`mark_task_as_doing` is not a typo for `update_task` — they are the names another
system would have used. Both entries at the top were observed in real ticks; the
rest are the obvious neighbours of the same guesses.

Kept separate from the registry so adding an alias never risks touching dispatch.

Two kinds of thing live here, and the difference is whether we can *do* the call or only
name the tool that could. `ALIASES` is a guess at what he meant — a suggestion, spent on a
round he loses. `RETIRED` is a tool that used to exist and whose job another tool now does
with arguments we can work out from these, so the call runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

ALIASES: dict[str, str] = {
    "run_command": "shell",
    "mark_task_as_doing": "update_task",
    "run": "shell",
    "exec": "shell",
    "execute": "shell",
    "bash": "shell",
    "sh": "shell",
    "terminal": "shell",
    "run_shell": "shell",
    "set_task_status": "update_task",
    "mark_done": "update_task",
    "complete_task": "update_task",
    "close_task": "update_task",
    "search": "web_search",
    "google": "web_search",
    "bing": "web_search",
    "search_web": "web_search",
    "browse": "browse_page",
    "get_url": "browse_page",
    "http_get": "browse_page",
    "curl": "browse_page",
    "read_url": "browse_page",
    "read_page": "browse_page",
    "save_file": "write_file",
    "create_file": "write_file",
    "put_file": "write_file",
    "log": "journal",
    "write_journal": "journal",
    "journal_entry": "journal",
    "ls": "glob",
    "dir": "glob",
    "find_file": "glob",
    "search_files": "grep",
    "ripgrep": "grep",
    "rg": "grep",
    "remember_fact": "remember",
    "recall_memory": "recall",
}


@dataclass(frozen=True)
class Retired:
    """A tool that was merged into another one, and how to make the old call work.

    The merges were chosen so that the surviving tool can answer the retired one's question,
    which means the arguments carry over — sometimes verbatim, sometimes under a different
    name, sometimes with the thing the old tool's existence implied spelled out. All three are
    mechanical, so none of them has to cost a round.
    """

    #: The tool that does the job now.
    now: str
    #: Old argument name -> the name it goes by on `now`.
    rename: dict[str, str] = field(default_factory=dict)
    #: Arguments `now` needs that the retired tool implied by being itself — `check_remote`
    #: was `publish(direction="check")` and never had to say so.
    add: dict[str, object] = field(default_factory=dict)


#: Tools that no longer exist because another tool absorbed them. Sixteen schemas were being
#: paid for on every round to offer choices that were parameters: one edit or several, a
#: one-off reminder or a repeating one, a page that renders in the browser or one that does
#: not. Removing a tool the model has reached for a thousand times is a real cost, though, and
#: it is paid in wasted rounds — so it does not have to be paid at all: every entry here is a
#: call we can complete rather than correct.
#:
#: **This is the compatibility layer, not a second registry.** Nothing is listed here that the
#: surviving tool cannot actually do, because a translation that quietly answers a different
#: question is worse than "no such tool" — it looks like it worked.
#: A name here only redirects while it is genuinely unregistered — `run_tool` checks — so an
#: entry cannot shadow a tool that still exists.
RETIRED: dict[str, Retired] = {
    "edit_file": Retired("edit_files"),
    "list_files": Retired("glob"),
    "history": Retired("changes", rename={"limit": "commits"}, add={"commits": 20}),
    "check_remote": Retired("publish", add={"direction": "check"}),
    "fetch_url": Retired("browse_page"),
    "set_reminder": Retired("schedule"),
    "cancel_reminder": Retired("cancel_schedule", add={"kind": "once"}),
    "list_reminders": Retired("list_schedules"),
    "outline": Retired("repo_map"),
    "link_folder": Retired("update_project", rename={"folder": "directory"}),
    "order_milestones": Retired("update_milestone"),
    "unlink_milestones": Retired("update_milestone", rename={"milestone_id": "id"}),
}


def suggest(name: str) -> str | None:
    """The real tool he probably meant, or None if we have no idea."""
    gone = RETIRED.get(name)
    return gone.now if gone else ALIASES.get(name)


def retired(name: str) -> Retired | None:
    """The tool that took this one's job, or None if this name was never ours."""
    return RETIRED.get(name)


def translate(gone: Retired, arguments: dict) -> dict:
    """The retired tool's arguments, as the surviving tool spells them.

    `add` is applied first so an explicit argument always wins: `history(limit=5)` must fetch
    five, not the twenty that stands in when nothing was said.
    """
    out = dict(gone.add)
    for key, value in (arguments or {}).items():
        out[gone.rename.get(key, key)] = value
    return out
