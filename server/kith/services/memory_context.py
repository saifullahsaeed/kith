"""What's 'present' in Kith's mind each turn.

Two layers reach the prompt on their own: what he keeps at the **front of his
mind** (memories he marked ``core`` — always there) and the **back of his mind**
(his most recent memories, which surface without effort). Everything else he
keeps but has to reach for, with the ``recall`` tool.
"""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo
from kith.kernel import clock
from kith.services.local_time import clock_line


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
        lines.append(f"- #{t['id']} [{t['status']}] ({t.get('priority', 'normal')}) {t['goal']}")
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


def presence_block(path: Path) -> str:
    """The '[Right now]' block injected each turn: the time, how long since he
    last acted, and any reminders waiting or newly due.

    Moved here from `domain/clock.py`, where it was the sole reason that module imported
    `kith.infra.db.repositories` at module scope — the one `domain -> infra` edge in the tree.
    It is not a time primitive. It is a system-prompt fragment that happens to open with the
    time, and it sits after `self_block` at the one call site that builds
    the prompt, which is what this module is for.
    """
    lines = ["[Right now]", clock_line()]

    mood = repo.self_model.get_mood(path)
    if mood.get("label"):
        felt = f"You feel {mood['label']} (energy {mood['energy']}/100)"
        felt += f" — {mood['note']}." if mood.get("note") else "."
        lines.append(felt)

    last = repo.activity.last_activity_at(path)
    since = clock.humanize_since(last)
    if since:
        lines.append(f"You last acted {since}.")

    pending = repo.reminders.list_reminders(path, status="pending")
    now = clock.now_iso()
    due = [r for r in pending if r["fire_at"] <= now]
    waiting = [r for r in pending if r["fire_at"] > now]
    if due:
        lines.append("Reminders that have come due — deal with them:")
        lines += [f"- (#{r['id']}) {r['note']}" for r in due]
    if waiting:
        nxt = waiting[0]
        extra = f" (+{len(waiting) - 1} more)" if len(waiting) > 1 else ""
        lines.append(f"Next reminder {clock.humanize_until(nxt['fire_at'])}: {nxt['note']}{extra}.")
    return "\n".join(lines)
