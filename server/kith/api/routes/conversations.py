"""Conversations: list them, read one, resume one, rename, remove."""

from __future__ import annotations

from flask import jsonify, request

from kith.api.blueprint import api
from kith.config import AGENT_DB_PATH
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
            "messages": conversations.messages(conversation_id),
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
