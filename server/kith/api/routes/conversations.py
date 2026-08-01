"""Conversations: list them, read one, resume one, rename, remove."""

from __future__ import annotations

from flask import jsonify, request

from kith.api.blueprint import api
from kith.config import AGENT_DB_PATH
from kith.infra.db import repositories as repo
from kith.services import conversations


@api.get("/conversations")
@api.doc(summary="Recent conversations", description="Newest first, for the history list.")
def list_conversations():
    try:
        limit = min(500, max(1, int(request.args.get("limit", 50))))
    except (TypeError, ValueError):
        limit = 50
    return jsonify(
        {
            "conversations": conversations.recent(AGENT_DB_PATH, limit),
            "storage": conversations.storage(),
        }
    )


@api.post("/conversations")
@api.doc(summary="Start a conversation")
def create_conversation():
    payload = request.get_json(silent=True) or {}
    return jsonify(conversations.start(AGENT_DB_PATH, str(payload.get("firstMessage") or "")))


@api.get("/conversations/search")
@api.doc(
    summary="Search everything either of you said",
    description=(
        "Across every transcript, newest first, one hit per conversation with a snippet. "
        "Only actual speech — reasoning, tool calls and token counts are in the files too "
        "and matching those would answer a question about a conversation with a stack trace."
    ),
)
def search_conversations():
    query = request.args.get("q") or ""
    try:
        limit = max(1, min(100, int(request.args.get("limit") or 40)))
    except ValueError:
        limit = 40
    return jsonify({"query": query, "hits": conversations.search(AGENT_DB_PATH, query, limit)})


@api.get("/conversations/<conversation_id>")
@api.doc(
    summary="One conversation",
    description=(
        "Its metadata, the messages in the shape /api/chat wants back, and the raw "
        "transcript entries — tool calls and token counts included, so a resumed "
        "conversation can be shown as it happened rather than as a list of paragraphs."
    ),
)
def read_conversation(conversation_id: str):
    try:
        meta = conversations.get(AGENT_DB_PATH, conversation_id)
    except KeyError:
        return jsonify({"error": f"no conversation {conversation_id}"}), 404
    return jsonify(
        {
            **meta,
            # `meta["messages"]` is a *count*, and this used to overwrite it with a *list* —
            # the same key meaning two different types depending on which endpoint you
            # asked. The count was silently lost here, and a client that read it off the
            # detail got an array where the listing gives a number. The turns keep the name
            # (every caller already reads it) and the count keeps its meaning under one that
            # says what it is.
            "messageCount": meta.get("messages", 0),
            "messages": conversations.messages(conversation_id),
            # What the interface renders: the turn's actual shape, not a paragraph of it.
            "timeline": conversations.timeline(conversation_id),
            "entries": conversations.read(conversation_id),
        }
    )


@api.patch("/conversations/<conversation_id>")
@api.doc(summary="Rename a conversation")
def rename_conversation(conversation_id: str):
    payload = request.get_json(silent=True) or {}
    title = str(payload.get("title") or "").strip()
    if not title:
        return jsonify({"error": "a title is required"}), 400
    try:
        return jsonify(conversations.rename(AGENT_DB_PATH, conversation_id, title))
    except KeyError:
        return jsonify({"error": f"no conversation {conversation_id}"}), 404


@api.put("/conversations/<conversation_id>/project")
@api.doc(
    summary="What this session is working on",
    description=(
        "Bind the conversation to a project, or pass null to unbind. This is what decides "
        "which project's `.kith/memory.md` he is shown here, and which tasks he advances "
        "when this session is left working — so two sessions on two projects stay out of "
        "each other's way."
    ),
)
def set_conversation_project(conversation_id: str):
    payload = request.get_json(silent=True) or {}
    raw = payload.get("projectId")
    try:
        project_id = int(raw) if raw not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        return jsonify({"error": "projectId must be a number, or null to unbind"}), 400
    if project_id is not None and repo.projects.get_project(AGENT_DB_PATH, project_id) is None:
        return jsonify({"error": f"there is no project #{project_id}"}), 404
    try:
        return jsonify(conversations.set_project(AGENT_DB_PATH, conversation_id, project_id))
    except KeyError:
        return jsonify({"error": f"no conversation {conversation_id}"}), 404


@api.delete("/conversations/<conversation_id>")
@api.doc(
    summary="Remove a conversation from the list",
    description=(
        "The transcript file stays on disk unless `purge=true`. Tidying a sidebar and "
        "destroying the only record of an afternoon's work should not be the same click."
    ),
)
def delete_conversation(conversation_id: str):
    purge = str(request.args.get("purge", "")).lower() in ("1", "true", "yes")
    conversations.delete(AGENT_DB_PATH, conversation_id, keep_file=not purge)
    return jsonify({"ok": True, "purged": purge})
