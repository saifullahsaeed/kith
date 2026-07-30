"""His notification channel."""

from __future__ import annotations

from flask import jsonify, request

from kith.api.blueprint import api
from kith.config import AGENT_DB_PATH
from kith.infra.db import repositories as repo


@api.get("/messages")
@api.doc(
    summary="Kith's messages to you",
    description="Things Kith has said on his own, newest first, plus the unread count.",
)
def messages_list():
    unread_only = request.args.get("unread") in ("1", "true", "yes")
    return jsonify(
        {
            "messages": repo.messages.list_messages(AGENT_DB_PATH, unread_only=unread_only),
            "unread": repo.messages.unread_message_count(AGENT_DB_PATH),
        }
    )


@api.post("/messages")
@api.doc(
    summary="Reply to Kith",
    description="Send a message to Kith on his own channel; he sees it on his next step and can respond.",
)
def messages_create():
    body = (request.get_json(silent=True) or {}).get("body", "").strip()
    if not body:
        return jsonify({"error": "empty message"}), 400
    return jsonify(repo.messages.add_message(AGENT_DB_PATH, body, sender="user"))


@api.post("/messages/read-all")
@api.doc(summary="Mark all read", description="Clear the unread badge.")
def messages_read_all():
    return jsonify({"marked": repo.messages.mark_all_messages_read(AGENT_DB_PATH)})


@api.post("/messages/<int:message_id>/read")
@api.doc(summary="Mark one read")
def message_read(message_id):
    return jsonify(repo.messages.mark_message_read(AGENT_DB_PATH, message_id) or {})
