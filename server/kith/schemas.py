"""Marshmallow schemas — these define the request/response shapes and, via
APIFlask, are what the generated OpenAPI spec is built from.
"""

from __future__ import annotations

from apiflask import Schema
from apiflask.fields import Boolean, Integer, List, Nested, String
from marshmallow import EXCLUDE


class ConfigSchema(Schema):
    """Chat parameters. Used as the /config response and as optional per-request
    overrides on /chat (all fields optional there)."""

    class Meta:
        unknown = EXCLUDE

    model = String(metadata={"description": "Model tag (Ollama) or model id (cloud)", "example": "qwen3:4b"})
    numCtx = Integer(metadata={"description": "Context window in tokens", "example": 40960})
    numPredict = Integer(metadata={"description": "Max output tokens (-1 = unlimited)", "example": 8192})
    system = String(metadata={"description": "System prompt / persona"})
    think = Boolean(metadata={"description": "Whether the model reasons before answering"})
    baseUrl = String(
        metadata={
            "description": "OpenAI-compatible cloud endpoint; blank = local Ollama",
            "example": "https://openrouter.ai/api/v1",
        }
    )
    apiKey = String(load_only=True, metadata={"description": "Cloud API key (write-only; never returned)"})
    apiKeySet = Boolean(dump_only=True, metadata={"description": "Whether a cloud API key is stored"})


class ChatMessageSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    role = String(required=True, metadata={"description": "'user' or 'assistant'"})
    content = String(required=True)


class ChatRequestSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    messages = List(
        Nested(ChatMessageSchema),
        required=True,
        metadata={"description": "Conversation so far (the backend prepends the system persona)"},
    )
    config = Nested(
        ConfigSchema,
        required=False,
        metadata={"description": "Optional per-request overrides of the defaults"},
    )


class HealthSchema(Schema):
    ok = Boolean(metadata={"description": "The backend is up"})
    ollamaReachable = Boolean(metadata={"description": "Ollama answered a probe"})


class AutonomyStatusSchema(Schema):
    running = Boolean()
    intervalSeconds = Integer(metadata={"description": "Seconds between self-directed ticks"})
    quietSeconds = Integer(metadata={"description": "Idle time required before a tick fires"})
    ticking = Boolean(metadata={"description": "A tick is running right now"})
    lastTick = String(allow_none=True)
    current = String(allow_none=True, metadata={"description": "What it's doing right now"})
    ticks = Integer(metadata={"description": "Total self-directed ticks run this session"})
    tokensIn = Integer(metadata={"description": "Prompt tokens spent by autonomy this session"})
    tokensOut = Integer(metadata={"description": "Response tokens spent by autonomy this session"})
    lastTickTokens = Integer(metadata={"description": "Tokens spent on the most recent tick"})


class AutonomyControlSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    action = String(required=True, metadata={"description": "'start', 'stop', or 'tick'"})
    intervalSeconds = Integer(required=False)
