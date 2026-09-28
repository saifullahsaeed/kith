"""What is worth keeping, and what is a query wearing a memory's clothes.

Measured on a real database of 51 memories: **49 of them `core`, and 27 lines of the core block
were the same fact re-saved on different days** —

    Current checkpoint (2026-08-12): Active high-priority Sadeef AI Task #99, "Move Odoo
    behavior into the plugin runtime," is working.

Nine near-copies of that. `core` means "always with you", so the block was 21,728 characters —
about 5,432 tokens — carried in every prompt of every round. On a forty-round turn that is a
quarter of a million tokens spent restating a fact the tasks table answers for free and which
stopped being true a week ago. `remember` was called 17 times; `recall` four.

Three things were wrong at once, and they compound: it was the wrong *kind* of thing to keep, it
was kept *again* instead of corrected, and it was kept at the level that costs the most.

**A refusal, not a better description.** `remember`'s own text already says "This is for FACTS
that outlive the conversation" and names `journal` for narration, and it was ignored — because
"Task #99 is active" genuinely is a fact that outlives the conversation. The description cannot
exclude it without excluding the category, and prose has a measured record here of not working:
`persona/35-how-you-spend-a-round.md` asked him to batch tool calls and the single-call share
went 77% -> 88%. So this is a gate in the shape `services/tasks.py` already uses — a refusal
that names what to do instead.

The one memory in that database worth having is *"the user has two GitHub accounts on this
machine, one work and one personal"*. Nothing else stores it and it does not go stale. That is
the shape.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from kith.infra.db import repositories as repo

#: Words that mean "this is where things stand", which is the thing the board is *for*.
#:
#: Widened after running the first version against the real database: it caught 36 of 50
#: snapshots and let 14 through, because the same fact had been written a dozen different ways
#: over a fortnight — "is complete and pushed", "canonical working context", "status as of",
#: "refactor state as of". They are all the same sentence with the nouns moved around, which is
#: what makes a keyword list the wrong instinct and a keyword list plus :data:`_BOARD_ROW` the
#: right one: the *subject* is the tell, not the phrasing.
_STATE = (
    "checkpoint",
    "current",
    "currently",
    "canonical",
    "active workstream",
    "active work",
    "in progress",
    "is working",
    "now working",
    "working on",
    "working context",
    "working state",
    "status is",
    "status as of",
    "state as of",
    "context as of",
    "as of",
    "is active",
    "is complete",
    "is done",
    "has shipped",
    "next up",
    "up next",
)

#: A reference to something the board already has a row for.
_BOARD_ROW = re.compile(
    r"\b(?:task|project|milestone)\s*#?\s*\d+"  # "Task #99", "project 6"
    r"|\b(?:task|project|milestone)\s+(?:id\s*)?\d+",
    re.I,
)

#: How close two memories have to be before the second one is a duplicate rather than a
#: refinement. Deliberately high: the nine copies in the measured database differed only in
#: their date and a clause of wording, so anything looser would let the next eight through.
_SAME = 0.86

#: The most `core` memories worth carrying. `core` is documented as "always with you" and is
#: read on every request, so it is the one level with a per-round price. Twenty is roughly two
#: pages — enough for the facts that shape how he works, and small enough that adding one is a
#: decision rather than a reflex. Forty-nine was a filing cabinet in the prompt.
MAX_CORE = 20

#: `list_memories` defaults to 50 rows. Both checks below have to see the whole shelf — a cap
#: would make them silently wrong at exactly the size the failure was measured at, 51.
_ALL = 100_000

#: How close by meaning a memory has to be to a project, and how far it has to lead the
#: runner-up, before its content alone reassigns it there. Measured on the live database with
#: nomic-embed-text: unrelated pairs already score 0.3-0.5, the two orphaned tooling memories
#: matched their nearest project at 0.572 and 0.608 with margins of 0.005 and 0.026, and the one
#: unambiguous case (the Harrier codename note) matched at 0.862 with a 0.199 lead. A bar of 0.62
#: with a 0.10 lead therefore keeps the ambiguous ones global and only reassigns the unmistakable
#: — which is why this signal sits *behind* an exact name or folder match, never ahead of it.
_SIMILAR_ENOUGH = 0.62
_CLEAR_MARGIN = 0.10

#: Below these, a project name or a folder basename is too generic to be evidence that a memory
#: is about that project. Four for a name ("Kith", "Vigil"), five for a folder ("vigil",
#: "sadeef-v2"): short enough to admit the real ones, long enough to keep an incidental word out.
_MIN_NAME = 4
_MIN_FOLDER = 5


def refuse(path: Path, content: str, level: str) -> dict | None:
    """A refusal to hand back, or None to let the memory through.

    Ordered cheapest-first, and each refusal names the tool that *should* have been used. A
    refusal that only says no teaches nothing: the measured failure was not carelessness, it was
    a reasonable-looking call made seventeen times with nothing ever pushing back.
    """
    text = (content or "").strip()
    if not text:
        return {
            "blocked": "Nothing to remember.",
            "next": "Say the fact itself — what is true, and why it will still be true later.",
        }

    snapshot = _looks_like_a_snapshot(text)
    if snapshot:
        return {
            "blocked": (
                "That is where things stand right now, not something to remember. The board "
                "already holds it, it is stale the moment the work moves, and saved as `core` "
                "it rides in every prompt from now on — a real database had nine copies of one "
                "task's status doing exactly that."
            ),
            "matched": snapshot,
            "next": (
                "If you want to know what is happening, ask: `list_tasks`, "
                "`list_projects`. If you want to leave a note about how this stretch of work "
                "went, that is `journal`. Remember the thing that will still be true when the "
                "task is closed — a decision and its reason, a constraint, how they like "
                "something done."
            ),
        }

    twin = _existing_twin(path, text)
    if twin is not None:
        return {
            "blocked": "You already remember this.",
            "existing": {"id": twin.get("id"), "content": str(twin.get("content") or "")[:300]},
            "next": (
                "If it has changed, `forget` that one and keep the new version — a second copy "
                "does not correct the first, it just means both are in front of you and one of "
                "them is wrong. If what you have now is genuinely additional, save only the "
                "part that is new."
            ),
        }

    if level == "core":
        held = _core_count(path)
        if held >= MAX_CORE:
            return {
                "blocked": f"You are already carrying {held} core memories, which is the limit.",
                "next": (
                    "`core` is what you carry in every prompt of every turn, so it is the one "
                    "level with a price per round. Save this as `recall` — it still comes back "
                    "when you reach for it — or `set_memory_level` something down to `recall` "
                    "first and say which, so the trade is deliberate."
                ),
            }
    return None


def _looks_like_a_snapshot(text: str) -> str:
    """The phrase that makes this a status report, or "".

    Both halves are required, and that is what keeps it from firing on real memories. "He
    prefers the checkpoint before the deploy" has the word and no row; "Task #99 covers the
    Odoo runtime" has the row and no state word. Neither is a snapshot. "Task #99 is working"
    is both, and is the whole measured failure.
    """
    lowered = text.lower()
    if not _BOARD_ROW.search(text):
        return ""
    for word in _STATE:
        if word in lowered:
            return word
    return ""


def _existing_twin(path: Path, text: str) -> dict | None:
    """A memory already held that says the same thing, or None.

    Semantic where it can be and literal where it cannot. Embedding is best effort everywhere
    else in this codebase — Ollama may be missing — and a duplicate check that silently stops
    working when the embedding model is absent is a check that stops working exactly on the
    machine where nobody notices.
    """
    from kith.services import embeddings

    vector = embeddings.embed(text)
    if vector:
        for found in repo.memories.semantic_search(path, vector, 3) or []:
            if float(found.get("score") or found.get("similarity") or 0) >= _SAME:
                return found
    normalised = _normalise(text)
    if len(normalised) < 40:
        return None  # too short for a literal comparison to mean anything
    for found in repo.memories.list_memories(path, limit=_ALL) or []:
        if _normalise(str(found.get("content") or "")) == normalised:
            return found
    return None


def _normalise(text: str) -> str:
    """Text with the parts that differ between two copies of one fact taken out — the date it
    was written, and how much whitespace it happened to have."""
    without_dates = re.sub(r"\d{4}-\d{2}-\d{2}", "", text.lower())
    return " ".join(without_dates.split())


def _core_count(path: Path) -> int:
    return sum(
        1 for one in repo.memories.list_memories(path, limit=_ALL) or [] if str(one.get("level")) == "core"
    )


# --------------------------------------------------------------------------- #
# Where a memory belongs — decided from what it is about, not where it was said.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Scope:
    """The project a new memory should be filed under, and a short line saying why.

    ``project_id`` is None for a global memory — a fact that holds whatever the work is. ``why``
    is handed back to the model in the tool result so the decision is visible and can be changed.
    """

    project_id: int | None
    why: str


def scope_for(
    path: Path,
    content: str,
    bound_project_id: int | None,
    scope: str = "",
    content_vector: list[float] | None = None,
) -> Scope:
    """Which project a new memory belongs to, decided from what it is *about*.

    Where a fact is learned is a weak proxy for what it is about. A note about the Harrier
    codename, settled in an unbound "what should we call it" chat, was stored global and then rode
    into every chat on the machine; a Sadeef One design-tokens note, and a missile-sim note, went
    the same way. So content leads here, and the chat's binding is only the default.

    Strongest signal first:

    1. An explicit ``scope`` the model set — ``global``, ``project``, or a project id — is obeyed.
       The override is there so the rule can be firm without being a trap; in practice it is
       rarely needed, because the inference below already lands the common cases.
    2. A project the content *names*, or whose *folder* it points at
       (:func:`_projects_pointed_at`). Lexical and exact, so it cannot flap with phrasing, and it
       is the signal that actually carries the measured misfiles — "Sadeef One design tokens"
       names its project, "~/Kith/missile-sim" points at one's folder. Two different projects
       pointed at is a tie the binding breaks if it is one of them, else meaning breaks it among
       only those.
    3. Only if nothing above decides: the content's meaning against each active project's profile,
       and only when one is unmistakably closest (:func:`embeddings.closest_project`). Gated hard,
       for the reason written on :data:`_SIMILAR_ENOUGH`.
    4. The binding, or global (NULL) for a chat in no project — the old, safe default.
    """
    from kith.services import embeddings

    projects = repo.projects.list_projects(path)
    by_id = {int(p["id"]): p for p in projects}

    explicit = _explicit_scope(scope, bound_project_id, by_id)
    if explicit is not None:
        return explicit

    pointed = _projects_pointed_at(content, projects)
    if len(pointed) == 1:
        pid = next(iter(pointed))
        return Scope(pid, f"filed under {_named(by_id, pid)} — its name or folder is in the note")
    if len(pointed) > 1:
        if bound_project_id in pointed:
            return Scope(
                bound_project_id, f"kept with this chat's project, {_named(by_id, bound_project_id)}"
            )
        among = [by_id[pid] for pid in pointed]
        pid = embeddings.closest_project(content, content_vector, among, bar=0.0, margin=0.0)
        pid = pid if pid is not None else sorted(pointed)[0]
        return Scope(pid, f"filed under {_named(by_id, pid)} — the closest of the projects it mentions")

    active = [p for p in projects if p.get("status") in ("active", "paused")]
    guessed = embeddings.closest_project(
        content, content_vector, active, bar=_SIMILAR_ENOUGH, margin=_CLEAR_MARGIN
    )
    if guessed is not None:
        return Scope(guessed, f"filed under {_named(by_id, guessed)} — closest by meaning")

    if bound_project_id is not None and bound_project_id in by_id:
        return Scope(bound_project_id, f"kept with this chat's project, {_named(by_id, bound_project_id)}")
    return Scope(None, "kept global — nothing in it tied it to a project")


def _explicit_scope(scope: str, bound_project_id: int | None, by_id: dict[int, dict]) -> Scope | None:
    """The scope the model asked for outright, or None to let the content decide.

    An unrecognised value is not an error to raise — it falls through to inference, because the
    model getting the argument slightly wrong should not lose the memory. A project id that does
    not exist becomes global with a note, for the same reason.
    """
    text = (scope or "").strip().lower()
    if not text:
        return None
    if text == "global":
        return Scope(None, "global because you set scope=global")
    if text == "project":
        if bound_project_id is not None and bound_project_id in by_id:
            return Scope(
                bound_project_id,
                f"kept with this chat's project, {_named(by_id, bound_project_id)} (scope=project)",
            )
        return Scope(None, "global — scope=project, but this chat is in no project")
    digits = text.lstrip("#")
    if digits.isdigit():
        pid = int(digits)
        if pid in by_id:
            return Scope(pid, f"filed under {_named(by_id, pid)} because you set scope={scope}")
        return Scope(None, f"global — you set scope={scope}, but there is no project #{pid}")
    return None


def _projects_pointed_at(content: str, projects: list[dict]) -> set[int]:
    """The ids of every project the content names outright or whose folder it points at.

    Lexical and exact on purpose: this is the signal that carries the real misfiles, and it must
    not flap with phrasing the way a similarity score does. A project is pointed at when its
    cleaned name appears as a whole phrase, or its folder's basename appears as a whole token;
    both are word-boundaried and case-insensitive. Names shorter than four characters and folders
    shorter than five are skipped as too generic to be evidence.
    """
    text = content or ""
    hits: set[int] = set()
    for project in projects:
        pid = int(project["id"])
        name = _clean_name(str(project.get("name") or ""))
        if len(name) >= _MIN_NAME and _mentions(text, name):
            hits.add(pid)
            continue
        directory = str(project.get("directory") or "")
        folder = os.path.basename(directory.rstrip("/")) if directory else ""
        if len(folder) >= _MIN_FOLDER and _mentions(text, folder):
            hits.add(pid)
    return hits


def _clean_name(name: str) -> str:
    """A project name cut down to the part a memory is likely to write: the head before a dash or
    colon descriptor, with any parenthetical dropped. "Kith Sec — AI pentest system (isolated
    lab)" becomes "Kith Sec"; "Asterion (working codename)" becomes "Asterion"."""
    head = re.split(r"\s+[—-]\s+|:", name, maxsplit=1)[0]
    head = re.sub(r"\([^)]*\)", "", head)
    return " ".join(head.split())


def _mentions(text: str, phrase: str) -> bool:
    """Whether ``phrase`` appears in ``text`` as a whole token, case-insensitively. The boundary
    treats a hyphen as part of the word so "missile-sim" matches inside "~/Kith/missile-sim" but
    not inside "missile-simulator"."""
    return re.search(rf"(?<![\w-]){re.escape(phrase)}(?![\w-])", text, re.I) is not None


def _named(by_id: dict[int, dict], pid: int | None) -> str:
    """A short human label for a project id, for the tool's explanation of where it filed the
    memory."""
    if pid is None:
        return "no project (global)"
    name = str((by_id.get(int(pid)) or {}).get("name") or "")
    return f"project #{pid}" + (f" ({name})" if name else "")
