"""Remembering, recalling, and forgetting."""

from __future__ import annotations

from pathlib import Path

from kith.domain.enums import MEMORY_LEVELS
from kith.infra.db import repositories as repo
from kith.services import embeddings, project_binding, remembering
from kith.tools.params import INT, STR
from kith.tools.registry import tool


@tool(
    "remember",
    "Keep something in memory. It carries across conversations. Its 'level' sets "
    "how present it is: 'core' stays at the front of your mind (always with you); "
    "'recall' (default) is kept, and comes back when you recall it. Your recent "
    "memories also surface on their own. "
    "This is for FACTS that outlive the conversation — a preference, a decision and why, "
    "what something is for. For what you did today and how it went, use `journal`: that is "
    "a log in time order, and a running narration kept here would crowd out the things you "
    "actually need to find again. "
    "NOT for where things stand. Which task is active, what its status is, what you are "
    "working on now — the board already answers that (`list_tasks`) and it is "
    "wrong the moment the work moves. Saving it is refused.",
    {
        "content": {**STR, "description": "The thing to remember."},
        "tags": {"type": "array", "items": STR, "description": "Optional labels."},
        "importance": {**INT, "description": "0-10."},
        "level": {**STR, "enum": list(MEMORY_LEVELS), "description": "core | recall (default)."},
        "scope": {
            **STR,
            "description": (
                "Where it belongs. Leave this out and it is decided from what the note is about "
                "— named or folder-pointed at a project goes there, otherwise it stays with this "
                "chat's project, or global if the chat is in none. Set 'global' to keep it in "
                "every chat, 'project' to force this chat's project, or a project id to file it "
                "elsewhere."
            ),
        },
    },
    required=("content",),
)
def remember(path: Path, args: dict):
    # Asked before anything is written, because every one of these is cheaper to refuse than to
    # undo: a snapshot goes stale on its own, a duplicate has to be told apart from the original
    # by hand, and a `core` memory is in every prompt until somebody notices.
    level = args.get("level") or "recall"
    content = args["content"]
    refusal = remembering.refuse(path, content, level)
    if refusal is not None:
        return refusal
    # Scoped by what the memory is *about*, not only by where the chat is bound. The binding was
    # the whole signal, so a fact settled in a quick unbound chat about one project became global
    # and joined every chat's pool — a 2,374-char design-system memory did exactly that, in every
    # prompt of every chat, until it was rescoped by hand. `scope_for` reads the content: a note
    # that names a project or points at its folder goes there even against the binding, the
    # binding stays the strong default otherwise, and the model can override outright with `scope`.
    #
    # The content is embedded once, here, and the vector is reused for both the scoping decision
    # and storage rather than embedded twice.
    vector = embeddings.embed(content)
    bound = project_binding.bound_project(path)
    scope = remembering.scope_for(path, content, bound, args.get("scope") or "", content_vector=vector)
    made = repo.memories.add_memory(
        path,
        content,
        args.get("tags"),
        args.get("importance") or 0,
        level,
        embedding=vector,
        project_id=scope.project_id,
    )
    # The result says where it went and how to move it — a silent choice the model cannot see is a
    # choice it cannot correct, which is how #95 stayed wrongly scoped in every prompt for a day.
    made["scope"] = {
        "project_id": scope.project_id,
        "note": scope.why,
        "to_change": (
            "It rides in this project's chats; any chat can still `recall` it. To move it: "
            "set scope='global' to keep it everywhere, or scope=<project id> to file it elsewhere."
            if scope.project_id is not None
            else "It is global — it rides in every chat. To tie it to a project, set scope=<project id>."
        ),
    }
    return made


@tool(
    "recall",
    "Reach into your memory for something you're not currently holding in mind.",
    {"query": {**STR, "description": "What to look for."}, "limit": INT},
    required=("query",),
)
def recall(path: Path, args: dict):
    return embeddings.recall(path, args["query"], args.get("limit") or 20)


@tool(
    "forget",
    "Delete a memory for good.",
    {"id": INT},
    required=("id",),
)
def forget(path: Path, args: dict):
    return {"forgotten": repo.memories.delete_memory(path, args["id"])}


@tool(
    "set_memory_level",
    "Move a memory between the front of your mind ('core', always present) and "
    "'recall' (kept, retrieved when you reach for it).",
    {"id": INT, "level": {**STR, "enum": list(MEMORY_LEVELS)}},
    required=("id", "level"),
)
def set_memory_level(path: Path, args: dict):
    return repo.memories.set_memory_level(path, args["id"], args["level"])
