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
from kith.services import memory_context
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
    """Prepend the persona plus the memory that's present right now, then turns."""
    out = []
    system = (config.system or "").strip()
    who = memory_context.self_block(AGENT_DB_PATH)
    if who:
        system = f"{system}\n\n{who}".strip()
    system = f"{system}\n\n{clock.presence_block(AGENT_DB_PATH)}".strip()
    people = memory_context.people_block(AGENT_DB_PATH)
    if people:
        system = f"{system}\n\n{people}".strip()
    channel = memory_context.messages_block(AGENT_DB_PATH)
    if channel:
        system = f"{system}\n\n{channel}".strip()
    projects = memory_context.projects_block(AGENT_DB_PATH)
    if projects:
        system = f"{system}\n\n{projects}".strip()
    work = memory_context.work_block(AGENT_DB_PATH)
    if work:
        system = f"{system}\n\n{work}".strip()
    present = memory_context.context_block(AGENT_DB_PATH)
    if present:
        system = f"{system}\n\n[Your memory right now]\n{present}".strip()
    # The chat turn-directive goes last so it's freshest in mind.
    system = f"{system}\n\n{CHAT_DIRECTIVE}".strip()
    if system:
        out.append({"role": "system", "content": system})
    for message in messages:
        if message.get("role") in ("user", "assistant"):
            out.append({"role": message["role"], "content": message.get("content", "")})
    return out


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
    messages = _build_messages(payload.get("messages") or [], config)

    def generate():
        try:
            for event in stream_agent(messages, config, ollama_host(), AGENT_DB_PATH):
                yield json.dumps(event) + "\n"
                if event.get("type") == "error":
                    return
            yield json.dumps({"type": "done"}) + "\n"
        except GeneratorExit:
            # Client disconnected (e.g. Stop was clicked) — end quietly.
            raise
        except Exception as exc:
            yield json.dumps({"type": "error", "message": str(exc)}) + "\n"

    return Response(
        generate(),
        mimetype="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
