"""Remembering, recalling, and forgetting."""

from __future__ import annotations

from pathlib import Path

from kith.domain.enums import MEMORY_LEVELS
from kith.infra.db import repositories as repo
from kith.services import embeddings
from kith.tools.params import INT, STR
from kith.tools.registry import tool


@tool(
    "remember",
    "Keep something in memory. It carries across conversations. Its 'level' sets "
    "how present it is: 'core' stays at the front of your mind (always with you); "
    "'recall' (default) is kept, and comes back when you recall it. Your recent "
    "memories also surface on their own.",
    {
        "content": {**STR, "description": "The thing to remember."},
        "tags": {"type": "array", "items": STR, "description": "Optional labels."},
        "importance": {**INT, "description": "0-10."},
        "level": {**STR, "enum": list(MEMORY_LEVELS), "description": "core | recall (default)."},
    },
    required=("content",),
)
def remember(path: Path, args: dict):
    return embeddings.remember(
        path, args["content"], args.get("tags"), args.get("importance") or 0, args.get("level") or "recall"
    )


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
