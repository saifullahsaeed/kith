"""The part of the prompt that is about *this project*, and nothing else.

Everything else in the present-state block is about **him** — the clock, his memories, his
channel with his person, what he last did. This is the one region about the work in hand, and
until it existed there was no such region: a conversation was shown every active project, every
active task across all of them, and — if more than one project was open — no project memory at
all, because the fallback that found it gave up whenever the answer was ambiguous.

The effect was exactly what you would predict. A new chat opened *inside* a project was told
"here are three projects and ten tasks, none of them labelled" and told nothing whatsoever about
the project it was in, so it went and read the other ones. It was not wandering. It was
answering the only question the context actually asked.

So the fix is a region, not a nudge:

* **Scoped.** Tasks, memory and references for one project, named at the top.
* **Complete.** What is done, what is in flight, what is waiting — the three questions a person
  opening a project asks, answerable without a tool call. A task list he has to *fetch* is a
  task list he answers from memory instead, which is the failure mode this codebase has a
  comment about in six other places.
* **Sole owner of the subject.** When this block is present, `memory_context.work_block` goes
  silent and `projects_block` shrinks to one line naming the others. Two blocks describing the
  same tasks differently is worse than either alone.

**Cost.** It sits in the region rewritten every turn, after the cached prefix, so adding to it
never invalidates the persona — but it is paid on every round, so every list here is capped and
every cap is a constant at the top of the file rather than a number buried in a loop.
"""

from __future__ import annotations

from pathlib import Path

from kith.infra import project_files
from kith.infra import workspace as sandbox
from kith.infra.db import repositories as repo
from kith.services import board_sync, project_memory

#: How many of each kind of task reach the prompt.
#:
#: In-flight is uncapped in spirit and capped in fact: more than a handful of things genuinely
#: being worked at once is a planning problem, and truncating the list is not the place to solve
#: it — but an unbounded loop over a column nobody is tidying is how a system prompt grows to ten
#: thousand tokens without anyone deciding to.
#:
#: Finished is the shortest of the three on purpose. "What is done" is a question about shape,
#: not inventory: the last few say what kind of work this is and how recently it moved, and the
#: count carries the rest. The full history is one `list_tasks` call away.
LIMITS = {"working": 8, "pending": 10, "done": 6}

#: How much of a project's own description reaches the prompt.
DESCRIPTION_CHARS = 600


def resolve(agent_db: Path, conversation_id: str = "") -> dict | None:
    """Which project this conversation is in, or None.

    The single answer to that question. It was inlined in `turn/prompt`, which meant the prompt's
    project region and everything else that wanted to scope itself to a project were free to
    disagree — and the two blocks describing the same board is precisely the bug this module
    exists to fix, so they must not resolve it separately.

    Asked of the session first, and of the board only as a fallback. `project_binding.adopt` is
    what writes the binding, and it refuses to move one once set, so the session's answer is the
    deliberate one and always wins.

    The fallback is "the only active project with a folder", and it is narrower than it looks: it
    fires exactly when there is no ambiguity to resolve. With two projects open it declines, and
    declining is correct — guessing which one somebody meant is how a conversation about one
    codebase ends up writing memory into another. What makes it *safe* now, and did not before,
    is that the first turn of a new chat carries its project in the request (see the
    `projectId` field on `/chat`), so a chat started inside a project is bound before this is
    ever asked.
    """
    try:
        if conversation_id:
            bound = repo.conversations.project_of(agent_db, conversation_id)
            if bound:
                found = repo.projects.get_project(agent_db, int(bound))
                if found:
                    return found
        active = [
            row
            for row in repo.projects.list_projects(agent_db)
            if row.get("status") == "active" and row.get("directory")
        ]
        return active[0] if len(active) == 1 else None
    except Exception:
        # A lookup that fails must not take down the turn. No project is a state the whole block
        # already handles — it is what an unbound conversation looks like.
        return None


def block(agent_db: Path, project: dict | None) -> str:
    """The whole project region, or "" when this conversation is not in a project.

    Empty rather than apologetic. A conversation with no project is an ordinary thing — a
    question, an errand, a decision about what to build next — and a paragraph explaining that
    it has no project would be paid for on every turn of every such conversation to say nothing.
    `memory_context` keeps its full listing in exactly that case, which is the right context for
    a chat that has not chosen yet.

    Every section at full length. The prompt does not use this — it takes `sections` and lets
    each one compete for the present-state budget on its own — but a caller that wants to show
    the region whole, like the context screen, wants exactly this.
    """
    return "\n\n".join(text for _, text in sections(agent_db, project)).strip()


def sections(agent_db: Path, project: dict | None) -> list[tuple[str, str]]:
    """The project region as ``(key, text)`` pairs, in reading order, empty ones left out.

    **Sections, not one block, and that is the whole of a fix.** The region used to reach the
    prompt as a single block that could not be trimmed, bidding for a 4,800-character budget
    that holds everything about the present. `memory.md` is deliberately uncapped, so the moment
    a project had learned anything the block outgrew the budget and was dropped *entire*.
    Measured on 2026-09-27: six of nine active projects — Vigil at 117,700 characters, Sadeef
    One at 76,335 — lost their board, their git standing and their memory together, and the only
    three that kept theirs were the ones whose `memory.md` was still the empty scaffold. The
    feature worked until it had something to say.

    Split, the cheap and decisive parts — which project, where its folder stands, what is in
    flight — can no longer be evicted by the one part that grows without bound. `memory.md` bids
    last and takes what is left; its heading says how long the file really is, so a section that
    stops early reads as the start of a file rather than the whole of one.
    """
    if not project or not project.get("id"):
        return []
    project_id = int(project["id"])
    name = str(project.get("name") or f"project #{project_id}")
    directory = str(project.get("directory") or "").strip()

    found = [
        (
            "project",
            "\n\n".join(
                p
                for p in (
                    _head(project_id, name, project, directory),
                    _tasks(agent_db, project_id, directory),
                )
                if p
            ),
        )
    ]
    if directory:
        # `project_memory.block` is the only one of these three that speaks when the folder is
        # missing, and it should be: it names the path and the fix. The other two stay quiet in
        # that case, because one broken link is one problem however many files it hid.
        #
        # No project name passed to it. The heading at the top of this region already says which
        # project this is, and "## What you already know about this project for Sadeef Capital
        # Portal Security Testing — Capital Call" is that same fact again at four times the
        # length.
        found.append(("project_memory", project_memory.block(directory)))
        found.append(("project_references", project_memory.references_block(directory)))
        found.append(("project_folder", _from_the_folder(agent_db, project_id, directory)))
    return [(key, text.strip()) for key, text in found if text and text.strip()]


def _head(project_id: int, name: str, project: dict, directory: str) -> str:
    """Which project this is, where it lives, and how its folder sits against the remote."""
    lines = [f"# The project you are in — #{project_id} {name}"]
    description = str(project.get("description") or "").strip()
    if description:
        # Capped because nothing caps it at the other end. A description is prose he wrote once
        # and is paid for on every turn of the project's life; the tail of a long one is never
        # the part that says what the project is.
        lines.append(
            description if len(description) <= DESCRIPTION_CHARS else description[:DESCRIPTION_CHARS] + "…"
        )
    if not directory:
        lines.append(
            "It has no folder linked, so nothing you write has a home that travels with it and "
            "there is no `.kith/` to read. If this project has code somewhere, point the project at "
            "it with `update_project` and a `directory` before you start."
        )
        return "\n".join(lines)

    lines.append(f"Folder: `{directory}` — relative paths land here.")
    # Local only, and it says so when it cannot be sure. See `workspace.standing`: the count is
    # of what the last fetch brought down, which is why the age of that fetch is part of the
    # same sentence rather than a detail somewhere else.
    try:
        said = sandbox.standing(directory)
    except Exception:
        said = ""
    if said:
        lines.append(said)
    return "\n".join(lines)


def _tasks(agent_db: Path, project_id: int, directory: str) -> str:
    """What is done, what is in flight, and what is waiting — on this project only.

    The three columns a person opening a project wants, and the reason this module exists.
    `memory_context.work_block` answered the middle one across every project at once with no
    project named on any row, which is not an answer to "what am I doing here" at all.
    """
    try:
        rows = [t for t in repo.tasks.list_tasks(agent_db) if t.get("project_id") == project_id]
    except Exception:
        return ""
    if not rows:
        return (
            "## The work\nNo tasks on this project yet. If what you are being asked is more than "
            "one sitting, file it before you start — work with no task is work that vanishes when "
            "this conversation ends."
        )

    working = [t for t in rows if t["status"] == "working"]
    pending = [t for t in rows if t["status"] in ("approved", "planning")]
    finished = sorted(
        (t for t in rows if t["status"] == "done"), key=lambda t: str(t.get("updated_at") or ""), reverse=True
    )
    try:
        progress = repo.tasks.checklist_progress(agent_db, [int(t["id"]) for t in working + pending])
    except Exception:
        progress = {}

    lines = [f"## The work — {len(finished)} done, {len(working)} in flight, {len(pending)} waiting to start"]
    if working:
        lines.append("")
        lines.append("**In flight — you are on these now:**")
        lines += [_task_line(t, progress, directory) for t in working[: LIMITS["working"]]]
        lines += _more(len(working), LIMITS["working"])
    if pending:
        lines.append("")
        lines.append("**Waiting to start:**")
        lines += [_task_line(t, progress, directory) for t in pending[: LIMITS["pending"]]]
        lines += _more(len(pending), LIMITS["pending"])
    if finished:
        lines.append("")
        lines.append("**Finished, most recent first:**")
        lines += [f"- #{t['id']} {t['goal']}" for t in finished[: LIMITS["done"]]]
        lines += _more(len(finished), LIMITS["done"])
    if finished and not working and not pending:
        # Said here because nothing else will. With milestones gone there is no roll-up that
        # notices a project running out of work, and closing one is their call, not his.
        lines.append("")
        lines.append(
            "Every task here is finished. If what the project set out to deliver is too, tell them "
            "it looks done and let them close it; if not, file what is left."
        )
    return "\n".join(lines)


def _more(total: int, shown: int) -> list[str]:
    """The line that admits a list was cut, or nothing. A truncated list with no such line reads
    as the whole set, which is how a task nobody has looked at in a month stays invisible."""
    return [f"- (+{total - shown} more — `list_tasks`)"] if total > shown else []


def _task_line(task: dict, progress: dict[int, tuple[int, int]], directory: str) -> str:
    """One task, with the two things that decide what to do about it: how far in it is, and
    where its plan is. The plan path is given rather than described — he has to open it, and a
    path he has to derive is a path he gets wrong."""
    task_id = int(task["id"])
    bits = [f"- #{task_id} [{task['status']}] ({task.get('priority', 'normal')}) {task['goal']}"]
    ticked = progress.get(task_id)
    if ticked:
        bits.append(f" — {ticked[0]}/{ticked[1]} checked off")
    if directory:
        try:
            plan = project_files.plan_path(directory, task_id)
            if plan.exists():
                bits.append(f", plan at `{project_files.KITH_DIR}/{project_files.WORK}/{plan.name}`")
        except Exception:
            pass
    return "".join(bits)


def _from_the_folder(agent_db: Path, project_id: int, directory: str) -> str:
    """Whatever somebody else has left in `.kith/` that this board has not taken in.

    Hoisted out of `list_tasks` and into the prompt, which is the whole of the second bug. It
    was attached to a tool, so the only way to learn that a colleague's work was sitting unread
    in the folder was to happen to call that tool — and the turns that most need to know are the
    ones that start by doing something else. `board_sync.note_for` is the cached form, because
    this is now on a per-turn path and the uncached one walks every brief in the folder.
    """
    try:
        return board_sync.note_for(agent_db, project_id, directory)
    except Exception:
        # Reading somebody else's folder is a courtesy, and `waiting_here` says the same at more
        # length: a turn that fails because of one would be the worse outcome by far.
        return ""


def named(agent_db: Path) -> str:
    """Every active project, named, for a conversation that is in none of them.

    The counterpart to `others`, and it exists because the assumption underneath the old
    unbound path turned out to be false. `memory_context.projects_block` and `work_block`
    handed an unbound chat every active project with its next step and every active task
    across all of them, on the reasoning that a chat which has not chosen a project wants the
    menu. Measured: 104 of 141 conversations on a real machine were unbound, and they are not
    pre-choice — they are questions, errands and one-off decisions that will never choose. The
    menu was being served to the overwhelming majority of conversations that had no use for it.

    So an unbound chat gets what a bound one gets about its siblings: names and a tool. He can
    still recognise that something belongs to a project, and going to look is one call.
    """
    try:
        rows = [row for row in repo.projects.list_projects(agent_db) if row.get("status") == "active"]
    except Exception:
        return ""
    if not rows:
        return ""
    shown = ", ".join(f"#{row['id']} {row['name']}" for row in rows[:6])
    rest = f", +{len(rows) - 6} more" if len(rows) > 6 else ""
    # No tool named. The line used to end "`list_projects` or `list_tasks` to look", and read as
    # an instruction: measured on 2026-09-27, unbound chats opened with one of those calls 16% of
    # the time, more than any other kind of chat — a "hey" answered with a board nobody asked
    # about. Recognising that something belongs to a project is what the names are for, and that
    # needs no call at all.
    return (
        f"[Your projects] {shown}{rest}. This conversation is not in any of them. Answer what is "
        "asked here; if it turns out to belong to one of them, say so and suggest opening a chat "
        "inside it."
    )


def others(agent_db: Path, project_id: int) -> str:
    """The one line naming the *other* active projects, or "".

    All that is left of the old `projects_block` once a conversation is in a project, and the
    shrinking is the point. That block listed every active project with its next step and, when
    a project looked finished, invited him to go and close it — every turn, in
    every conversation, whichever project it was actually about. An invitation to work on
    something else, repeated on every round, is not neutral context.

    They are still *named*, though, rather than hidden. He should be able to notice that a thing
    he is being asked about belongs to a different project — `project_binding.foreign_project`
    refuses the write and tells him to start a new conversation, and that refusal reads as
    arbitrary if he did not know the other project existed. Names and a tool, nothing more.
    """
    try:
        rows = [
            row
            for row in repo.projects.list_projects(agent_db)
            if row.get("status") == "active" and int(row["id"]) != int(project_id)
        ]
    except Exception:
        return ""
    if not rows:
        return ""
    named = ", ".join(f"#{row['id']} {row['name']}" for row in rows[:6])
    rest = f", +{len(rows) - 6} more" if len(rows) > 6 else ""
    return (
        f"[Your other projects] {named}{rest}. Not this conversation's work — a new conversation "
        "if you need to *change* one."
    )
