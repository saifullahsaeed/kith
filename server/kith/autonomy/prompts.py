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
from kith.infra import workspace as sandbox
from kith.infra.db import repositories as repo
from kith.services import tuning

# `_tick_prompt` used to live here: one prompt covering "a reminder is due", "here are your
# tasks" and "you are caught up". Every branch of it has a dedicated builder now —
# `_due_prompt`, `_focus_prompt`, and an idle path that calls no model at all — and it had no
# caller left. Deleted rather than kept for reference: two prompts for one situation is how
# you end up editing the one that is not running.


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
    notes = _task_notes(detail.get("comments") or [])
    if notes:
        lines.append(
            "What you have already recorded on this task — read this before touching the "
            "codebase, it is where you got to last time:"
        )
        lines += notes
    # Your working file — the memory of this task that survives between turns.
    # Surface it so you resume from it instead of re-gathering from scratch.
    # Relative, so it lands in whatever folder he is actually working in. It used to be
    # an absolute container path, which on this machine cannot be created at all.
    #
    # `.kith/work/`, matching the persona and `project_files.ensure` — which both said so while
    # this line said `work/`. The person moved their `work/` folder into `.kith/` and every tick
    # went on instructing him to write outside it, so the notes ended up split down the middle:
    # tasks 34–41 under `.kith/work/`, tasks 42–50 under `work/`, and the prompt could only ever
    # see one of the two.
    work_path = f".kith/work/task-{detail['id']}.md"
    saved = _read_working_file(work_path)
    if saved is None:
        # The old location, still read so the files already written there are not orphaned by
        # moving the path. Only ever read from — anything new goes to `.kith/work/`.
        legacy = _read_working_file(f"work/task-{detail['id']}.md")
        if legacy is not None:
            saved = legacy
    if saved is not None:
        lines.append(f"Your working file ({work_path}) — what you've saved so far:")
        lines.append(_handoff(saved, work_path) if saved.strip() else "  (empty)")
    else:
        lines.append(
            f"You have no working file for this task yet. Create {work_path} and keep your findings "
            "there as you go, so you never lose progress or start over."
        )
    # Your own recent steps, framed as a loop check — deep enough to reveal a long loop
    # (three was too shallow to see a twenty-tick one) and worded so a repeat prompts a
    # change of course rather than another neutral "last steps" list he reads straight past.
    recent = repo.journal.list_journal(AGENT_DB_PATH, tuning.value("handoff_steps"))
    if recent:
        lines.append(
            "Your recent steps (most recent first) — if these look like the same attempt again, "
            "STOP repeating it: change approach decisively, or say what's blocking you and hand "
            "it back. Doing the same thing again costs money and moves nothing:"
        )
        lines += [f"  · {e['entry'][:200]}" for e in recent]
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


#: How much of the working file to carry into a tick. 1,500 was the old figure and it cost
#: him every resume: the task section is only about 1,000 tokens of a 16,000-token prompt, so
#: there was room to spare and nothing being bought with the frugality.
_HANDOFF_CHARS = 4_000

#: How much of his own commentary on a task rides in the prompt. Generous, because it is the
#: single most valuable thing available to a tick and it was almost entirely absent.
_NOTES_CHARS = 6_000


def _task_notes(comments: list[dict]) -> list[str]:
    """His own record of this task, most of it, in the order he wrote it.

    This was `comments[-1]["body"][:160]` — one note, cut at a hundred and sixty characters.
    Measured across the board: 170,736 characters of findings he had written on his own tasks,
    of which a tick was ever shown **4.1%**.

    It is the worst loss in the whole handoff, because commenting progress is what the persona
    tells him to do and therefore where his best notes go. Task #43 — the one that took ten
    ticks — had fourteen comments holding 5,689 characters he never saw again, against a working
    file of 2,659 bytes. He wrote more than twice as much into the channel he could not read back
    as into the one he could, then re-derived it by reading the codebase again: 54% of his file
    reads in one session were of a file he had already read that turn.

    Newest-biased but chronological. The budget is spent from the most recent note backwards, so
    what survives is the recent work — then printed oldest-first, because these are a narrative
    and reading a narrative backwards costs him a round working out the order.

    A note from his person is never dropped. Theirs are rare, they are usually a correction, and
    "sqlite is not being used any more, we moved to postgres" is precisely the sentence that must
    not fall off the end of a budget.
    """
    if not comments:
        return []
    theirs = [c for c in comments if str(c.get("author") or "") == "user"]
    mine = [c for c in comments if str(c.get("author") or "") != "user"]

    kept: list[dict] = list(theirs)
    spent = sum(len(str(c.get("body") or "")) for c in theirs)
    for comment in reversed(mine):
        body = str(comment.get("body") or "")
        # The most recent note always goes in, however long it is. A budget that can return
        # nothing is worse than the bug being fixed: it would put a tick back to knowing
        # nothing about its own last attempt, which is the whole failure.
        first = not any(c not in theirs for c in kept)
        if not first and spent + len(body) > _NOTES_CHARS:
            break
        kept.append(comment)
        spent += len(body)

    order = {id(c): i for i, c in enumerate(comments)}
    kept.sort(key=lambda c: order.get(id(c), 0))
    dropped = len(comments) - len(kept)

    out = []
    if dropped > 0:
        out.append(f"  […{dropped} earlier note(s) not shown — `view_task` has all of them.]")
    for comment in kept:
        who = "YOU ASKED" if str(comment.get("author") or "") == "user" else "you wrote"
        body = " ".join(str(comment.get("body") or "").split())
        out.append(f"  · [{who}] {body}")
    return out


def _handoff(saved: str, path: str) -> str:
    """His own notes, from the end.

    The end, not the beginning, and that one word was the whole bug. He appends to this file as
    he works, so the newest thing in it — usually a literal "### NEXT" list of what he was about
    to do — is at the bottom. The prompt showed the first 1,500 characters of it. On a 6,805-char
    file that is the oldest 22%, so his own handoff to himself was reliably the part he could not
    see, and he resumed by re-reading the codebase instead. That is the App.jsx-twice and
    styles.css-three-times in the feed.

    The head is not missed: the definition of done and the plan are already in the prompt as the
    task's description and checklist, so the top of this file is mostly a restatement of them.
    What is unique to it is the recent work.
    """
    text = saved.strip()
    if len(text) <= _HANDOFF_CHARS:
        return text
    return (
        f"  […{len(text) - _HANDOFF_CHARS:,} earlier characters are still in {path} if you "
        f"need them. The most recent part:]\n" + text[-_HANDOFF_CHARS:]
    )


def _read_working_file(path: str) -> str | None:
    """The task's working file if it exists, else None. Best-effort — never let a
    missing file or a sleepy sandbox break the tick. (path is internal/controlled,
    always `.kith/work/task-<int>.md` — or the pre-move `work/task-<int>.md` — under
    whatever folder he is working in, so a plain cat is safe.)"""
    try:
        result = sandbox.run_command(f'cat "{path}" 2>/dev/null')
        if result.exit_code != 0:
            return None
        return result.output
    except Exception:
        return None


# `_task_line` went with `_tick_prompt`, its only caller.


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
    usually a missing tool/access, not a pointless task. Only a lingering question
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
    # There used to be a second thing to give up on here: an open curiosity, dropped when he
    # was going in circles. Curiosities are gone, and with them the only case where "stuck"
    # meant something other than stuck on a task.
    return ""


#: How much of one tool argument travels down the activity feed. A file's entire contents
#: must not go over the wire to be cut off by CSS at the far end.
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


def _breakdown_prompt(milestone: dict) -> str:
    """A milestone whose turn it is, with nothing under it to do.

    Its own step, rather than something bolted onto the work prompt, because it is a
    different job: not "make progress on this" but "work out what progress would consist
    of". Handing him a task prompt with no task produced the thing this exists to stop —
    he treated an inert project as an empty board and went off to reflect.
    """
    return (
        f"Your project “{milestone['project']}” is at the milestone “{milestone['title']}”, "
        f"and there is nothing filed under it — so nothing can happen, however long you roam.\n\n"
        "Break just this milestone into tasks. One sitting's work each, concrete enough that "
        "you could finish one and know you had. Hang them off milestone "
        f"{milestone['id']} (`add_task` with `milestone_id`), and give them a real order of "
        "importance rather than all the same.\n\n"
        "Only this milestone. The ones after it are not yours to plan yet — you will know more "
        "about them once this is done than you do now, and a plan written too early is fiction "
        "you will feel obliged to follow.\n\n"
        "This is the whole step. File the tasks, say what you set up, and stop — the first of "
        "them is the next tick's job, not this one's."
    )
