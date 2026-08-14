"""His notification channel."""

from __future__ import annotations

from flask import jsonify, request

from kith.api.blueprint import api
from kith.infra.db import repositories as repo
from kith.settings import AGENT_DB_PATH


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
            # Per kind, over the whole channel rather than the page above it — what the
            # panel's clear options promise to remove.
            "counts": repo.messages.count_messages_by_kind(AGENT_DB_PATH),
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


@api.post("/messages/clear")
@api.doc(
    summary="Clear alerts",
    description=(
        'Delete his messages in bulk. Pass `kinds` (e.g. ["note"]) to clear only those; omit '
        "it to clear the lot. Your own replies to him are not the target."
    ),
)
def messages_clear():
    raw = (request.get_json(silent=True) or {}).get("kinds")
    if raw is None:
        kinds = None
    elif isinstance(raw, list) and all(isinstance(one, str) for one in raw):
        kinds = [one.strip() for one in raw if one.strip()]
    else:
        return jsonify({"error": "kinds must be a list of strings"}), 400
    return jsonify({"deleted": repo.messages.delete_messages(AGENT_DB_PATH, kinds)})


@api.post("/messages/<int:message_id>/read")
@api.doc(summary="Mark one read")
def message_read(message_id):
    return jsonify(repo.messages.mark_message_read(AGENT_DB_PATH, message_id) or {})


@api.get("/notify")
@api.doc(
    summary="How much he may interrupt you",
    description=(
        "The threshold and the options. Every message is recorded whatever this says — the "
        "level decides what counts as unread and what posts a desktop notification."
    ),
)
def get_notify():
    from kith.infra import notify

    return jsonify(notify.snapshot())


@api.post("/notify")
@api.doc(summary="Set the threshold", description="One of all, needs_you, reachout.")
def set_notify():
    from kith.infra import notify

    payload = request.get_json(silent=True) or {}
    try:
        notify.set_level(str(payload.get("level") or ""))
    except ValueError:
        return jsonify({"error": "level must be all, needs_you or reachout"}), 400
    return jsonify(notify.snapshot())
