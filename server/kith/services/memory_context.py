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


def context_block(
    path: Path, core_limit: int = 30, recent_limit: int = 8, project_id: int | None = None
) -> str:
    """What he is holding in mind, scoped to the conversation.

    `project_id` is the project the conversation is in, or None. Global memories surface either
    way; a project's own memories join them only in that project's chats; another project's are
    left for `recall`. Before this the block was the same on every turn whatever the subject —
    so a security-testing chat carried a different project's Odoo internals and nothing of its
    own. See `repositories.memories._scope`.
    """
    core = repo.memories.memories_by_level(path, "core", core_limit, project_id)
    recent = repo.memories.recent_memories(path, recent_limit, project_id)
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
    plan and knows when a project is complete.

    **For a conversation that is not in a project**, where a menu is the right answer because
    nothing has been chosen yet. A conversation that *is* in one gets `project_context.others`
    instead: the same projects, named and no more. This block's closing nudge — "all milestones
    met and no active tasks, consider marking this project done" — was appearing on every turn
    of every conversation whatever it was about, which is an invitation to go and work on
    something else, repeated on every round.
    """
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
    ongoing tasks instead of out of nowhere. Highest priority first.

    **For a conversation that is not in a project.** Once one is, `project_context` owns this
    subject entirely and says far more about far less: three columns for one project instead of
    one column across all of them. Two blocks describing the same board differently is worse
    than either, so the caller picks one.

    Every row now names its project, and that was the bug rather than a nicety. The line read
    `- #31 [working] (high) Connect the client access flows to the lifecycle API` — nothing on
    it says which codebase that is, so ten of them are ten invitations to open the wrong one.
    Tasks belonging to no project say so out loud for the same reason: "unattached" is a fact
    about a task, and one that quietly reads as "yours" is how work gets picked up in a folder
    that has nothing to do with it.
    """
    tasks = repo.tasks.active_tasks(path)
    if not tasks:
        return ""
    try:
        named = {int(p["id"]): str(p.get("name") or "") for p in repo.projects.list_projects(path)}
    except Exception:
        named = {}
    lines = ["[What you're already working on — most important first]"]
    for t in tasks[:10]:
        project_id = t.get("project_id")
        whose = named.get(int(project_id)) if project_id else ""
        where = f" — {whose}" if whose else " — not on any project"
        lines.append(f"- #{t['id']} [{t['status']}] ({t.get('priority', 'normal')}) {t['goal']}{where}")
    if len(tasks) > 10:
        lines.append(f"- (+{len(tasks) - 10} more — `list_tasks`)")
    return "\n".join(lines)


def messages_block(path: Path, project_id: int | None = None) -> str:
    """The recent back-and-forth on his own channel with his person, and a nudge
    if they've said something he hasn't answered.

    Scoped to the conversation's project, like memory. The channel was global — the last eight
    messages on every turn whatever the chat was about — so a brand-new chat about anything
    opened with a security engagement's reach-outs ("I need an answer: the June test accounts
    are dead", "Finished Wave 3C"). Now a project's chat shows that project's channel plus the
    global notes, and the rest stays in the inbox, which shows everything regardless.

    The unanswered-message nudge below is deliberately *not* scoped: a reply you owe your person
    is a reply you owe them whatever chat you are in, so it surfaces everywhere.
    """
    recent = repo.messages.list_messages(path, limit=8, project_id=project_id, scoped=True)  # newest first
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


def presence_block(path: Path) -> str:
    """The '[Right now]' block injected each turn: the time, how long since he
    last acted, and any reminders waiting or newly due.

    Moved here from `domain/clock.py`, where it was the sole reason that module imported
    `kith.infra.db.repositories` at module scope — the one `domain -> infra` edge in the tree.
    It is not a time primitive. It is a system-prompt fragment that happens to open with the
    time, and it is assembled at the one call site that builds the prompt, which is what
    this module is for.
    """
    lines = ["[Right now]", clock_line()]

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
