"""Talking to him."""

from __future__ import annotations

import json

from flask import Response

from kith.api.blueprint import api
from kith.autonomy import runner as autonomy
from kith.config import AGENT_DB_PATH, default_config, merge_overrides, ollama_host
from kith.domain import clock
from kith.schemas import (
    ChatRequestSchema,
)
from kith.services import conversations, memory_context
from kith.services.agent_loop import stream_agent

CHAT_DIRECTIVE = (
    "Your person is talking to you directly. Follow these rules for this turn:\n\n"
    "1. CAPTURE, DON'T DO (most important). When they give you a piece of work — 'research X', 'look into Y', "
    "'find Z', 'build …' — your DEFAULT is to CAPTURE it, not carry it out now. Call add_task with a clear "
    "goal, then STOP: do NOT call web_search, fetch_url, shell, or other work tools for it. Reply that you've "
    "added it and roughly when you'll get to it. Do the work right now ONLY if they explicitly signal "
    "immediacy — 'now', 'right now', 'go ahead', 'do it now', 'today' — or it's a trivial one-step thing you "
    "can answer immediately.\n"
    "2. ORIENT FIRST. Read what's above — your notes, your current tasks, your projects — and connect their "
    "message to what's already going on. If it relates to an existing task, say so instead of starting over.\n"
    "2b. FILE IT UNDER A PROJECT. When you capture a task, place it: if it belongs to one of your existing "
    "projects, link it there (project_id); if it's a new, substantial line of work that doesn't fit any project, "
    "create_project first and add the task under it (a one-off trivial errand can stay project-less). This keeps "
    "the board organised instead of a flat pile of tasks.\n"
    "3. NO GUESSING. If the request is ambiguous (e.g. a name you don't know), ask which they mean or say "
    "plainly what you don't know.\n"
    "4. Only ever use tools that actually exist in your tool list. Never invent a tool.\n"
    "5. Always finish with a short, clear, human reply. Never leave them with raw tool output or your own "
    "thinking-out-loud."
)


def _build_messages(messages, config):
    """The persona, then the turns, then the state he is in right now.

    The order is a caching decision, and it is worth more than it looks. Everything a
    provider can reuse has to sit in an unchanging prefix: the persona and the turn
    directive never change, so they go first and alone.

    What follows them changes constantly — the clock to the minute, his mood, how long
    since he last acted, whatever memory is present — and it used to be concatenated into
    the same system message. That is what made a repeated "hey" cost full price twice:
    the moment he replies, `last_activity_at` moves, "1 hour ago" becomes "just now", and
    the message is no longer byte-identical. Providers that cache automatically match at
    message granularity, so one changed word at the end discarded ~8,000 cacheable tokens
    at the start. Measured: identical consecutive messages, 0% cached; with the volatile
    part moved out, 99.7%.

    Putting it last is also the better prompt. It is the freshest thing he knows, and the
    directive already sat at the end for exactly that reason.
    """
    out = []
    persona = (config.system or "").strip()
    if persona:
        # Byte-identical on every request Kith ever makes. Nothing else may join it.
        out.append({"role": "system", "content": f"{persona}\n\n{CHAT_DIRECTIVE}".strip()})
    for message in messages:
        if message.get("role") not in ("user", "assistant"):
            continue
        out.append(_with_attachments(message))
    now = _present_state()
    if now:
        out.append({"role": "system", "content": now})
    return out


def _with_attachments(message: dict) -> dict:
    """Turn a message with attachments into multimodal content.

    Only images become content parts. A model that lists `image` in its modalities takes
    them inline; anything else — a PDF, a spreadsheet, a zip — is named and pointed at
    instead, because he has a whole computer now and reading a file with his own tools is
    both more capable and more honest than pretending the model can see it. He can open a
    spreadsheet with python, and no vision model can.
    """
    text = message.get("content", "") or ""
    attachments = [a for a in (message.get("attachments") or []) if isinstance(a, dict)]
    if not attachments:
        return {"role": message["role"], "content": text}

    images = [a for a in attachments if str(a.get("kind")) == "image" and a.get("data")]
    others = [a for a in attachments if a not in images]
    if others:
        named = ", ".join(str(a.get("name") or "a file") for a in others)
        # A path, not a payload: it is already on the machine he works on.
        text = f"{text}\n\n[They attached: {named}. Read it with your own tools.]".strip()
    if not images:
        return {"role": message["role"], "content": text}

    parts: list[dict] = [{"type": "text", "text": text}] if text else []
    parts += [{"type": "image_url", "image_url": {"url": str(image["data"])}} for image in images]
    return {"role": message["role"], "content": parts}


def _present_state() -> str:
    """Everything about him that is true only at this moment."""
    blocks = [
        memory_context.self_block(AGENT_DB_PATH),
        clock.presence_block(AGENT_DB_PATH),
        memory_context.people_block(AGENT_DB_PATH),
        memory_context.messages_block(AGENT_DB_PATH),
        memory_context.projects_block(AGENT_DB_PATH),
        memory_context.work_block(AGENT_DB_PATH),
    ]
    present = memory_context.context_block(AGENT_DB_PATH)
    if present:
        blocks.append(f"[Your memory right now]\n{present}")
    return "\n\n".join(block for block in blocks if block).strip()


def _save_reply(conversation_id: str, parts: list[str]) -> None:
    """Store what he said, once, at the end.

    Per-delta would mean a line in the transcript per token. The parts are accumulated and
    joined instead — and written on the error and disconnect paths too, because a turn that
    was interrupted is exactly the one whose half-answer you want to keep.
    """
    text = "".join(parts).strip()
    if text:
        conversations.record(AGENT_DB_PATH, conversation_id, "assistant", text)


@api.post("/chat")
@api.input(ChatRequestSchema, arg_name="payload")
@api.doc(
    summary="Stream an agent turn",
    description=(
        "Runs the agentic loop (the model may call its memory/notes/journal/task "
        "tools) and streams `application/x-ndjson`: one JSON object per line.\n"
        '- `{"type":"delta","role":"reasoning"|"text","text":"..."}`\n'
        '- `{"type":"tool_call","id":"...","name":"...","arguments":{...}}`\n'
        '- `{"type":"tool_result","id":"...","name":"...","result":{...}}`\n'
        '- `{"type":"stats","stats":{...}}` (one per model request, so several per turn — '
        "`uncachedTokens` is the prompt with cache hits removed)\n"
        '- `{"type":"error","message":"..."}`\n'
        '- `{"type":"done"}` (terminal)'
    ),
    responses={200: "NDJSON stream of agent events"},
)
def chat(payload):
    autonomy.note_user_activity()  # defer self-directed ticks while you're here
    config = merge_overrides(default_config(), payload.get("config") or {})
    history = payload.get("messages") or []
    messages = _build_messages(history, config)

    # Which conversation this belongs to. Opened on the first message rather than when the
    # window opens, so idly launching the app does not litter the history with empties.
    conversation_id = str(payload.get("conversationId") or "").strip()
    latest = next((m.get("content", "") for m in reversed(history) if m.get("role") == "user"), "")
    if not conversation_id:
        conversation_id = conversations.start(AGENT_DB_PATH, latest)["id"]
    conversations.record(AGENT_DB_PATH, conversation_id, "user", latest)

    def generate():
        # Tell the client which conversation it is in before anything else, so a chat
        # started without an id can attach itself and reload into the same place.
        yield json.dumps({"type": "conversation", "id": conversation_id}) + "\n"
        reply: list[str] = []
        try:
            for event in stream_agent(
                messages, config, ollama_host(), AGENT_DB_PATH, conversation_id=conversation_id
            ):
                if event.get("type") == "delta" and event.get("role") == "text":
                    reply.append(event.get("text") or "")
                elif event.get("type") in ("tool_call", "tool_result", "stats"):
                    conversations.record_event(conversation_id, event["type"], event)
                yield json.dumps(event) + "\n"
                if event.get("type") == "error":
                    conversations.record_event(conversation_id, "error", {"message": event.get("message")})
                    _save_reply(conversation_id, reply)
                    return
            _save_reply(conversation_id, reply)
            yield json.dumps({"type": "done"}) + "\n"
        except GeneratorExit:
            # Client disconnected (e.g. Stop was clicked) — end quietly, but keep what he
            # had already said. A stopped answer is still an answer that was given.
            _save_reply(conversation_id, reply)
            raise
        except Exception as exc:
            yield json.dumps({"type": "error", "message": str(exc)}) + "\n"

    return Response(
        generate(),
        mimetype="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
