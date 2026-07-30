"""Tool names Kith reaches for that do not exist.

Spelling distance cannot fix these: `run_command` and `shell` share no letters, and
`mark_task_as_doing` is not a typo for `update_task` — they are the names another
system would have used. Both entries at the top were observed in real ticks; the
rest are the obvious neighbours of the same guesses.

Kept separate from the registry so adding an alias never risks touching dispatch.
"""

from __future__ import annotations

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
    "get_url": "fetch_url",
    "http_get": "fetch_url",
    "curl": "fetch_url",
    "read_url": "fetch_url",
    "read_page": "fetch_url",
    "save_file": "write_file",
    "create_file": "write_file",
    "put_file": "write_file",
    "add_note": "take_note",
    "write_note": "take_note",
    "log": "journal",
    "write_journal": "journal",
    "journal_entry": "journal",
    "ls": "list_files",
    "dir": "list_files",
    "find_file": "list_files",
    "search_files": "grep",
    "ripgrep": "grep",
    "rg": "grep",
    "remember_fact": "remember",
    "recall_memory": "recall",
}


def suggest(name: str) -> str | None:
    """The real tool he probably meant, or None if we have no idea."""
    return ALIASES.get(name)
