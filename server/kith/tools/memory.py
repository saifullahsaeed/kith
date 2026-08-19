"""Remembering, recalling, and forgetting."""

from __future__ import annotations

from pathlib import Path

from kith.domain.enums import MEMORY_LEVELS
from kith.infra.db import repositories as repo
from kith.services import embeddings, remembering
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
    "working on now — the board already answers that (`list_tasks`, `view_task`) and it is "
    "wrong the moment the work moves. Saving it is refused.",
    {
        "content": {**STR, "description": "The thing to remember."},
        "tags": {"type": "array", "items": STR, "description": "Optional labels."},
        "importance": {**INT, "description": "0-10."},
        "level": {**STR, "enum": list(MEMORY_LEVELS), "description": "core | recall (default)."},
    },
    required=("content",),
)
def remember(path: Path, args: dict):
    # Asked before anything is written, because every one of these is cheaper to refuse than to
    # undo: a snapshot goes stale on its own, a duplicate has to be told apart from the original
    # by hand, and a `core` memory is in every prompt until somebody notices.
    level = args.get("level") or "recall"
    refusal = remembering.refuse(path, args["content"], level)
    if refusal is not None:
        return refusal
    return embeddings.remember(path, args["content"], args.get("tags"), args.get("importance") or 0, level)


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
