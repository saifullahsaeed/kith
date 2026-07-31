"""What he is actually asked, at the start of each kind of tick.

Every one of these is a string built from the current state of his world — what is on the
board, what is waiting on him, what he was in the middle of. They are pure functions: state
in, prose out, nothing touched. That is what makes them safe to read and change without
reasoning about the loop, and it is why they are here rather than in runner.py, which is
about *when* he thinks rather than *what he is asked*.

The wording matters more than it looks like it should. These are the only instructions he
gets for a tick nobody is watching, so a vague one produces a vague hour of work.
"""

from __future__ import annotations

from datetime import UTC, datetime

from kith.config import AGENT_DB_PATH
from kith.domain import clock
from kith.infra import workspace as sandbox
from kith.infra.db import repositories as repo


def _tick_prompt(active_tasks: list[dict], due_reminders: list[dict] | None = None) -> str:
    if due_reminders:
        notes = "\n".join(f"- {r['note']}" for r in due_reminders)
        return (
            f"A reminder you set has come due:\n{notes}\n\n"
            "Act on it now — that's why you set it. Journal what you did."
        )
    if active_tasks:
        lines = "\n".join(_task_line(t) for t in active_tasks[:8])
        return (
            f"Your tasks, most important first:\n{lines}\n\n"
            "Take one concrete step toward the most important one now — move it to 'doing' if you're "
            "starting it, and to 'done' when it's finished."
        )
    return (
        "You have no active tasks right now — and that's fine; being caught up is a good state. "
        "Don't invent busywork or re-poke things you've already finished. Rest: reflect, follow a "
        "genuine curiosity, or just note that you're clear. Only set a new task if something truly matters."
    )


def _focus_prompt(detail: dict, active: list[dict]) -> str:
    """The chosen task in full, plus a phase hint for what to do next."""
    lines = [f"Your focus task — #{detail['id']} [{detail.get('priority', 'normal')}]: {detail['goal']}"]
    if detail.get("description"):
        lines.append(f"Definition of done: {detail['description']}")
    checklist = detail.get("checklist") or []
    if checklist:
        lines.append("Checklist:")
        lines += [f"  [{'x' if c['done'] else ' '}] {c['text']}" for c in checklist]
    delivered = detail.get("deliverables") or []
    if delivered:
        lines.append("Delivered so far: " + ", ".join(d["title"] for d in delivered))
    comments = detail.get("comments") or []
    if comments:
        last = comments[-1]
        lines.append(f"Latest note ({last['author']}): {last['body'][:160]}")
    # Your working file — the memory of this task that survives between turns.
    # Surface it so you resume from it instead of re-gathering from scratch.
    # Relative, so it lands in whatever folder he is actually working in. It used to be
    # an absolute container path, which on this machine cannot be created at all.
    work_path = f"work/task-{detail['id']}.md"
    saved = _read_working_file(work_path)
    if saved is not None:
        lines.append(f"Your working file ({work_path}) — what you've saved so far:")
        lines.append(saved[:1500] if saved.strip() else "  (empty)")
    else:
        lines.append(
            f"You have no working file for this task yet. Create {work_path} and keep your findings "
            "there as you go, so you never lose progress or start over."
        )
    # Your own recent train of thought, so you pick up where you left off after a
    # gap or a disruption instead of re-deciding from scratch.
    recent = repo.journal.list_journal(AGENT_DB_PATH, 3)
    if recent:
        lines.append("Your last steps (most recent first):")
        lines += [f"  · {e['entry'][:160]}" for e in recent]
    if len(active) > 1:
        lines.append(f"(You have {len(active) - 1} other task(s) queued; this is the top one.)")

    unchecked = [c for c in checklist if not c["done"]]
    if not checklist:
        hint = "No plan yet — if this needs more than one step, break it into a checklist now; otherwise just do it and deliver."
    elif unchecked:
        hint = f"Do this next: “{unchecked[0]['text']}” — then tick it off and comment your progress."
    else:
        hint = "All steps are done — verify it meets the definition of done, attach the deliverable, then mark the task done."
    lines += ["", hint]
    return "\n".join(lines)


def _read_working_file(path: str) -> str | None:
    """The task's working file if it exists, else None. Best-effort — never let a
    missing file or a sleepy sandbox break the tick. (path is internal/controlled,
    always work/task-<int>.md under his folder, so a plain cat is safe.)"""
    try:
        result = sandbox.run_command(f'cat "{path}" 2>/dev/null')
        if result.exit_code != 0:
            return None
        return result.output
    except Exception:
        return None


def _task_line(task: dict) -> str:
    bits = [f"#{task['id']}", f"[{task['status']}]", f"({task.get('priority', 'normal')})"]
    if task.get("due_at"):
        bits.append(f"due {clock.humanize_until(task['due_at'])}")
    return f"- {' '.join(bits)} {task['goal']}"


def _due_prompt(reminders: list[dict], schedules: list[dict]) -> str:
    parts = []
    if schedules:
        jobs = "\n".join(f"- {s['note']}" for s in schedules)
        parts.append(f"A standing job of yours is due now:\n{jobs}")
    if reminders:
        notes = "\n".join(f"- {r['note']}" for r in reminders)
        parts.append(f"A reminder you set has come due:\n{notes}")
    return (
        "\n\n".join(parts) + "\n\nDo it now — actually carry it out with your tools, and if it's for your "
        "person, reach_out with the result. Journal what you did."
    )


def _reflection_prompt(active_tasks: list[dict]) -> str:
    recent = repo.journal.list_journal(AGENT_DB_PATH, 12)
    lately = "\n".join(f"- {j['entry']}" for j in recent) or "- (nothing logged yet)"
    goals = "\n".join(f"- #{t['id']} [{t['status']}] {t['goal']}" for t in active_tasks[:8]) or "- (none)"
    return (
        f"Lately, in your own words, you have:\n{lately}\n\n"
        f"Open goals right now:\n{goals}\n\n"
        "Read that back honestly. Is it going somewhere, or are you repeating yourself? "
        "Prune what's stale and choose a direction worth growing into."
    )


def _resume_prompt(awaiting: list[dict]) -> str:
    task = awaiting[0]
    comments = repo.tasks.list_task_comments(AGENT_DB_PATH, task["id"])
    last = comments[-1]["body"] if comments else ""
    return (
        f'On task #{task["id"]} — “{task["goal"]}” — your person just replied:\n"{last}"\n\n'
        "Act on it now: do the work, comment your progress on the task, move it forward "
        "(to 'doing', or 'done' if finished), and attach a deliverable if you made something."
    )


def _reply_prompt(pending: list[dict]) -> str:
    msgs = "\n".join(f"- {m['body']}" for m in pending)
    return (
        f"Your person just wrote to you:\n{msgs}\n\n"
        "Respond to them now with reach_out — and if they need something done, do it first, then reply."
    )


def _breakout_prompt(active: list[dict]) -> str:
    recent = repo.journal.list_journal(AGENT_DB_PATH, 6)
    lately = "\n".join(f"- {j['entry']}" for j in recent) or "- (nothing)"
    goals = "\n".join(f"- #{t['id']} {t['goal']}" for t in active[:6]) or "- (none)"
    return (
        f"You keep doing much the same thing:\n{lately}\n\nOpen goals:\n{goals}\n\n"
        "It isn't moving forward. Change course decisively, or let it go."
    )


def _latest_journal_id() -> int:
    """Newest journal row id, or 0 — used to tell whether he journalled himself."""
    try:
        rows = repo.journal.list_journal(AGENT_DB_PATH, 1)
    except Exception:
        return 0
    return int(rows[0].get("id") or 0) if rows else 0


def _give_up(active: list[dict]) -> str:
    """Escalate the thing he's stuck on. A task he can't move gets set to 'waiting'
    and his person is notified (with a link) — NOT dropped, because a stall is
    usually a missing tool/access, not a pointless task. Only a lingering curiosity
    (nothing owed to anyone) is quietly let go."""
    if active:
        task = active[0]
        repo.tasks.update_task(AGENT_DB_PATH, task["id"], status="waiting")
        repo.messages.add_message(
            AGENT_DB_PATH,
            f"I'm stuck on “{task['goal']}” (task #{task['id']}) and can't move it on my own — "
            "I've set it aside for you. Open it to see what's blocking me.",
            link=f"/tasks/{task['id']}",
            kind="stuck",
        )
        repo.journal.add_journal(
            AGENT_DB_PATH,
            f"Set '{task['goal']}' to waiting and flagged it for my person — I couldn't move it alone.",
        )
        return task["goal"]
    exploring = [
        c for c in repo.curiosities.list_curiosities(AGENT_DB_PATH) if c["status"] in ("open", "exploring")
    ]
    if exploring:
        curiosity = exploring[0]
        repo.curiosities.update_curiosity(AGENT_DB_PATH, curiosity["id"], status="dropped")
        repo.journal.add_journal(
            AGENT_DB_PATH, f"Let go of '{curiosity['topic']}' — going in circles. Moving on."
        )
        return curiosity["topic"]
    return ""


def _consolidation_prompt() -> str:
    recent = repo.journal.list_journal(AGENT_DB_PATH, 25)
    lately = "\n".join(f"- {j['entry']}" for j in recent) or "- (nothing yet)"
    mems = repo.memories.list_memories(AGENT_DB_PATH, 50)
    held = "\n".join(f"- #{m['id']} [{m['level']}] {m['content']}" for m in mems) or "- (none)"
    return (
        f"Lately, in your journal:\n{lately}\n\n"
        f"What you currently hold in memory:\n{held}\n\n"
        "Settle it: keep what matters, strengthen what recurs, clear the noise."
    )


def _curiosity_prompt() -> str:
    projects = [p for p in repo.projects.list_projects(AGENT_DB_PATH) if p.get("status") == "active"]
    active = repo.tasks.active_tasks(AGENT_DB_PATH)
    open_ones = [
        c for c in repo.curiosities.list_curiosities(AGENT_DB_PATH) if c["status"] in ("open", "exploring")
    ]
    parts = []
    if projects:
        parts.append("Your projects: " + ", ".join(p["name"] for p in projects[:5]))
    if active:
        parts.append("Open work: " + "; ".join(t["goal"][:50] for t in active[:5]))
    if open_ones:
        parts.append(
            "Threads you're already pulling:\n"
            + "\n".join(f"- #{c['id']} {c['topic']}" for c in open_ones[:6])
        )
    context = "\n\n".join(parts)
    return (
        (context + "\n\n" if context else "")
        + "What would make you better at this work? Wonder about a problem you've hit, a "
        "technique or tool that would help, or the domain your work lives in — then dig in "
        "and form a view you can actually use. Keep it tied to the work, not a private hobby."
    )


#: How much of one argument value to carry into the live feed. Long enough for a path or a
#: short command to arrive whole, short enough that a file's entire contents does not travel
#: down the event stream to be truncated by CSS at the other end.
_ARG_CHARS = 120


def _short_args(arguments: dict) -> dict:
    """Arguments trimmed for display, as data rather than as a sentence.

    Values only — the interface decides which argument is the subject of which verb, because
    that is a presentation question and it changes when the wording changes.
    """
    trimmed: dict[str, str] = {}
    for key, value in (arguments or {}).items():
        text = value if isinstance(value, str) else str(value)
        text = " ".join(text.split())
        trimmed[key] = text[: _ARG_CHARS - 1] + "…" if len(text) > _ARG_CHARS else text
    return trimmed


def _describe_call(name: str, arguments: dict) -> str:
    if not arguments:
        return f"{name}()"
    parts = []
    for key, value in arguments.items():
        text = value if isinstance(value, str) else str(value)
        if len(text) > 40:
            text = text[:40] + "…"
        parts.append(f"{key}={text}")
    return f"{name}({', '.join(parts)})"


def _now() -> str:
    return datetime.now(UTC).isoformat()
