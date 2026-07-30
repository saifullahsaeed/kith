"""What's 'present' in Kith's mind each turn.

Two layers reach the prompt on their own: what he keeps at the **front of his
mind** (memories he marked ``core`` — always there) and the **back of his mind**
(his most recent memories, which surface without effort). Everything else he
keeps but has to reach for, with the ``recall`` tool.
"""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo


def context_block(path: Path, core_limit: int = 30, recent_limit: int = 8) -> str:
    core = repo.memories.memories_by_level(path, "core", core_limit)
    recent = repo.memories.recent_memories(path, recent_limit)
    if not core and not recent:
        return ""

    lines: list[str] = []
    if core:
        lines.append("At the front of your mind (always with you):")
        lines += [f"- {m['content']}" for m in core]
    if recent:
        if lines:
            lines.append("")
        lines.append("In the back of your mind (recent):")
        lines += [f"- {m['content']}" for m in recent]
    return "\n".join(lines)


def projects_block(path: Path) -> str:
    """His active projects and the next step on each roadmap — so he follows the
    plan and knows when a project is complete."""
    active = [p for p in repo.projects.project_overview(path) if p["status"] == "active"]
    if not active:
        return ""
    lines = ["[Your projects — the bigger goals you're driving]"]
    for p in active:
        prog = (
            f"{p['milestones_done']}/{p['milestones_total']} milestones"
            if p["milestones_total"]
            else "no milestones yet"
        )
        lines.append(f"- #{p['id']} {p['name']} — {prog}, {p['tasks_active']} active task(s)")
        nxt = next((m for m in p["milestones"] if m["status"] != "done"), None)
        if nxt:
            due = f" (target {nxt['target_at'][:10]})" if nxt.get("target_at") else ""
            lines.append(f"    next milestone: {nxt['title']}{due}")
        elif p["milestones_total"] and p["tasks_active"] == 0:
            lines.append("    all milestones met and no active tasks — consider marking this project done.")
    return "\n".join(lines)


def work_block(path: Path) -> str:
    """What he's already working on — so a chat message lands in the context of his
    ongoing tasks instead of out of nowhere. Highest priority first."""
    tasks = repo.tasks.active_tasks(path)
    if not tasks:
        return ""
    lines = ["[What you're already working on — most important first]"]
    for t in tasks[:10]:
        due = f" · due {t['due_at'][:10]}" if t.get("due_at") else ""
        lines.append(f"- #{t['id']} [{t['status']}] ({t.get('priority', 'normal')}) {t['goal']}{due}")
    return "\n".join(lines)


def messages_block(path: Path) -> str:
    """The recent back-and-forth on his own channel with his person, and a nudge
    if they've said something he hasn't answered."""
    recent = repo.messages.list_messages(path, limit=8)  # newest first
    if not recent:
        return ""
    lines = ["[Your channel with your person — recent messages]"]
    for m in reversed(recent):  # oldest -> newest
        who = "You" if m.get("sender", "kith") == "kith" else "Them"
        lines.append(f"{who}: {m['body']}")
    pending = repo.messages.pending_user_messages(path, limit=5)
    if pending:
        lines.append("")
        lines.append(
            "↳ They've written to you and haven't heard back. If it deserves a reply, "
            "answer them with reach_out (and act on it if it needs doing)."
        )
    return "\n".join(lines)


def self_block(path: Path) -> str:
    """Who Kith understands himself to be — always present, so he stays himself."""
    me = repo.self_model.get_self(path)
    if not me.get("identity") and not me.get("profile"):
        return ""
    lines = ["[Who you are, as you've come to see yourself]"]
    if me.get("identity"):
        lines.append(me["identity"])
    if me.get("profile"):
        lines += me["profile"].splitlines()
    return "\n".join(lines)


def people_block(path: Path) -> str:
    """Who Kith is with — the people he's come to know, so he doesn't relearn them."""
    people = repo.people.list_people(path)
    if not people:
        return ""
    lines = ["[Who you know]"]
    for person in people:
        header = person["name"]
        if person["relationship"]:
            header += f" — {person['relationship']}"
        lines.append(header)
        if person["profile"]:
            lines += [f"  {line}" for line in person["profile"].splitlines()]
    return "\n".join(lines)
