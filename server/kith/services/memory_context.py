"""What's 'present' in Kith's mind each turn.

Two layers reach the prompt on their own: what he keeps at the **front of his
mind** (memories he marked ``core`` — always there) and the **back of his mind**
(his most recent memories, which surface without effort). Everything else he
keeps but has to reach for, with the ``recall`` tool.
"""

from __future__ import annotations

from pathlib import Path

from kith.domain import present
from kith.infra.db import repositories as repo
from kith.infra.db.vectors import cosine
from kith.kernel import clock, session_context
from kith.services import embeddings
from kith.services.local_time import clock_line

#: How close by meaning a recall memory has to be to what is being discussed before it rides
#: along a turn unasked. Calibrated on the live database (58 memories) with nomic-embed-text:
#: the worst of ten greetings ("hey", "thanks", "yo", "good morning", …) peaked at 0.501 cosine
#: against the whole shelf, while a real question's on-subject memories sat at 0.55-0.86. A bar
#: of 0.55 therefore clears every greeting by a margin and still admits a handful of on-topic
#: memories per question (measured: 6 for a design question, 3 for a git one, 2 for the missile
#: sim). It is higher than the lexical bar (:data:`present.RELEVANT_ENOUGH` = 0.34) because
#: nomic's cosines have a high floor — unrelated pairs already score ~0.3-0.5 — so the two
#: numbers are not comparable and must not be reconciled. Not a knob: `recall` reaches anything
#: below it in one call.
EMBED_RELEVANT = 0.55


def context_block(
    path: Path,
    core_limit: int = 30,
    recent_limit: int = 8,
    project_id: int | None = None,
    about: str = "",
) -> str:
    """What he is holding in mind, scoped to the conversation and to its subject.

    `project_id` is the project the conversation is in, or None. Global memories surface either
    way; a project's own memories join them only in that project's chats; another project's are
    left for `recall`. See `repositories.memories._scope`.

    **`about` is the second scope, and it is the one that was missing.** Project scoping cannot
    help a chat that is in no project — which is most of them — and it cannot help a global
    memory at all, since a global memory is in scope everywhere by definition. So the eight
    most-recent non-core memories arrived on every turn regardless of subject, and with
    `MEMORY_LEVELS = ("core", "recall")` "non-core" is exactly "recall": the level whose own
    tool description promises it is "retrieved when you reach for it". A conversation about
    nothing opened carrying a missile simulator's physics notes.

    Now core arrives unconditionally — that is what the level means — and a recall memory has to
    have something to do with what is being discussed. `recall` still reaches every one of them,
    which is the whole point of the split: what arrives free is small and on-topic, and the rest
    is one call away rather than a tax on every round.

    An empty `about` therefore yields core only. That is correct rather than a degradation: the
    first turn of a brand-new chat is precisely the conversation this exists to keep clean.

    **Which recall memory earns a slot is now decided by meaning, not shared words.** All memories
    are already embedded (`services/embeddings`), nomic-embed-text runs locally, and `about` is a
    few lines, so one embed call per turn — under a strict timeout, with the old lexical overlap
    kept as the fallback — buys relevance that does not flicker with phrasing. "login" now reaches
    a memory about "authentication"; it did not when the test was word overlap. See
    :func:`_relevant_recall`.
    """
    core = repo.memories.memories_by_level(path, "core", core_limit, project_id)
    # Fetched by recency and then *scored* by subject, rather than fetched by subject. Recency is
    # the cheap SQL bound that keeps this from scoring the whole table on every turn; the scoring
    # below is what decides. Widened past `recent_limit` so the choice is made over a real
    # candidate pool — taking the most recent eight and then scoring them would let one busy
    # afternoon in another project push every relevant memory out of contention. Carries each
    # candidate's vector, which is what the semantic scoring needs and `recent_memories` withholds.
    candidates = repo.memories.recent_recall_with_vectors(path, max(recent_limit * 4, 32), project_id)
    earned = _relevant_recall(candidates, about)[:recent_limit]

    if not core and not earned:
        return ""

    lines: list[str] = []
    if core:
        lines.append("At the front of your mind (always with you):")
        lines += [f"- {m['content']}" for m in core]
    if earned:
        if lines:
            lines.append("")
        lines.append("Related to what you're doing right now:")
        lines += [f"- {m['content']}" for m in earned]
    return "\n".join(lines)


def _relevant_recall(candidates: list[tuple[dict, object]], about: str) -> list[dict]:
    """The recall memories with enough to do with ``about`` to ride along, best first.

    ``candidates`` are ``(memory, vector)`` pairs, all recall (core is fetched and added
    separately). Embedding-first: ``about`` is embedded once, under the strict timeout in
    :func:`embeddings.embed_for_injection` because this is the prompt path, and each candidate is
    scored by cosine against its stored vector, kept above :data:`EMBED_RELEVANT`.

    When no candidate is embedded, or the embed times out or Ollama is gone, the whole decision
    falls back to :func:`present.eligible_memories` — the lexical overlap that was the rule before
    this. Reusing it rather than re-deriving it is why that function is still the one definition of
    "related by words", and the fallback is why recall never silently stops working on a machine
    without embeddings, the same reason :func:`remembering._existing_twin` gives. A single
    un-embedded straggler during a backfill is scored lexically in place, ordered against the
    cosine-scored rest by how far each clears *its own* bar — so 0.55 and 0.34 are never pretended
    to be the same scale.
    """
    about = (about or "").strip()
    has_vectors = any(vector for _, vector in candidates)
    about_vec = embeddings.embed_for_injection(about) if (about and has_vectors) else None

    if about_vec is None:
        # No usable embedding for this turn — defer wholesale to the lexical selector.
        return present.eligible_memories([memory for memory, _ in candidates], about)

    scored: list[tuple[float, dict]] = []
    for memory, vector in candidates:
        if vector:
            score, bar = cosine(about_vec, vector), EMBED_RELEVANT
        else:
            score, bar = present.relevance(about, str(memory.get("content") or "")), present.RELEVANT_ENOUGH
        if score >= bar:
            scored.append((score - bar, memory))
    scored.sort(key=lambda row: -row[0])
    return [memory for _, memory in scored]


def projects_block(path: Path) -> str:
    """His active projects and where each one's board stands.

    **For a conversation that is not in a project**, where a menu is the right answer because
    nothing has been chosen yet. A conversation that *is* in one gets `project_context.others`
    instead: the same projects, named and no more. This block used to close with a nudge — "all
    milestones met and no active tasks, consider marking this project done" — which appeared on
    every turn of every conversation whatever it was about, an invitation to go and work on
    something else repeated on every round. It is not coming back with milestones gone.
    """
    active = [p for p in repo.projects.project_overview(path) if p["status"] == "active"]
    if not active:
        return ""
    lines = ["[Your projects — the bigger goals you're driving]"]
    for p in active:
        board = (
            f"{p['tasks_done']} done, {p['tasks_working']} working, {p['tasks_approved']} approved, "
            f"{p['tasks_planning']} planning"
            if p["tasks_total"]
            else "no tasks yet"
        )
        lines.append(f"- #{p['id']} {p['name']} — {board}")
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

    # Where the message was typed, when it was typed in a terminal rather than the window.
    # Here rather than in the message itself because it has the same lifetime as the clock: it
    # is true of this turn and no other, and somebody who runs `kith send` from a different
    # folder next time must not have the old one still in the transcript as something they
    # said. See `session_context.arriving_from` for why it grants nothing.
    typed_in = session_context.sent_from_directory()
    if typed_in:
        lines.append(f"This message was sent from a terminal, in {typed_in}.")

    lines += _workflow_lines()

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


def _workflow_lines() -> list[str]:
    """Whether he may start a workflow this turn, and whether one is already running here.

    Said here, with the clock, because both are true of this turn and no other: workflow mode can
    be switched off between two messages, and a run that was going an hour ago has finished. A
    tool description cannot carry either — it is the same on every turn — and the decision to
    fan out is made before any tool is called, so this is the only place the fact can be when it
    matters.
    """
    from kith.services import workflows

    lines: list[str] = []
    why = session_context.workflow_permission()
    if why == "mode":
        lines.append(
            "Workflow mode is ON in this chat: you may start a workflow (run_workflow) without being "
            "asked. Use one when the work splits into independent parts big enough to be worth "
            "their own agents — several packages, a survey then builders then a review. Do small or "
            "tightly coupled work yourself; a fan-out on a one-turn task is wasted money."
        )
    elif why == "asked":
        lines.append(
            "They asked for a workflow (/workflow). Use one if the work really splits; if it is small "
            "enough to do in this turn, do it directly and say in a sentence why you did not fan out."
        )
    try:
        running = workflows.running(session_context.current())
    except Exception:
        running = None
    if running is not None:
        working = sum(1 for one in running.agents.values() if one.get("state") != "done")
        where = f", in {running.phase}" if running.phase else ""
        lines.append(
            f"A workflow is running in this chat: `{running.name}` ({running.id}){where}, "
            f"{working} agent{'s' if working != 1 else ''} working. check_workflow shows where it is."
        )
    return lines
