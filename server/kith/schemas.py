"""Marshmallow schemas — these define the request/response shapes and, via
APIFlask, are what the generated OpenAPI spec is built from.
"""

from __future__ import annotations

from apiflask import Schema
from apiflask.fields import Boolean, Dict, Float, Integer, List, Nested, String
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
    effort = String(
        metadata={"description": "Reasoning effort: '', 'low', 'medium' or 'high'", "example": "medium"}
    )
    baseUrl = String(
        metadata={
            "description": "OpenAI-compatible cloud endpoint; blank = local Ollama",
            "example": "https://openrouter.ai/api/v1",
        }
    )
    apiKey = String(load_only=True, metadata={"description": "Cloud API key (write-only; never returned)"})
    capabilities = Dict(
        dump_only=True,
        metadata={"description": "What this model accepts: images, files, reasoning, modalities"},
    )
    apiKeySet = Boolean(dump_only=True, metadata={"description": "Whether a cloud API key is stored"})


class ChatMessageSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    role = String(required=True, metadata={"description": "'user' or 'assistant'"})
    content = String(required=True)
    attachments = List(
        Dict(),
        required=False,
        metadata={
            "description": "Images or files sent with this message: "
            "[{kind: 'image'|'file', name, mediaType, data}] where data is a data: URL. "
            "Only accepted for models whose provider lists the modality."
        },
    )


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
    conversationId = String(
        required=False,
        metadata={
            "description": "Which conversation this turn belongs to. Omit to start one; "
            "the stream's first frame reports the id that was created.",
        },
    )


class HealthSchema(Schema):
    ok = Boolean(metadata={"description": "The backend is up"})
    ollamaReachable = Boolean(metadata={"description": "Ollama answered a probe"})


class AutonomyStatusSchema(Schema):
    working = List(
        String(),
        metadata={"description": "Ids of the sessions taking steps right now"},
    )
    ticking = Boolean(metadata={"description": "A tick is running right now"})
    stopping = Boolean(metadata={"description": "A stop was asked for and the running step is winding up"})
    lastTick = String(allow_none=True)
    current = String(allow_none=True, metadata={"description": "What it's doing right now"})
    ticks = Integer(metadata={"description": "Total self-directed ticks run this session"})
    tokensIn = Integer(metadata={"description": "Prompt tokens spent by autonomy this session"})
    tokensOut = Integer(metadata={"description": "Response tokens spent by autonomy this session"})
    # A field absent from this schema is dropped from the response, silently — which is
    # how the two below shipped as zeros to a UI that was reading them correctly.
    tokensUncached = Integer(
        metadata={"description": "Prompt tokens with cache hits removed — what was actually read"}
    )
    lastTickTokens = Integer(metadata={"description": "Tokens spent on the most recent tick"})
    lastTickUncached = Integer(
        metadata={"description": "Tokens the most recent tick actually had to read plus write"}
    )
    costUsd = Float(
        metadata={
            "description": "What autonomy has actually cost this session, in dollars, as "
            "billed by the provider rather than estimated from token counts"
        }
    )


class AutonomyControlSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    action = String(
        required=True,
        metadata={
            "description": "'start' (this session keeps working), 'stop' (it stops — all "
            "sessions when no conversationId is given), 'tick' (one step now), or 'cancel' "
            "(abandon the step in flight, leaving the session working)"
        },
    )
    conversationId = String(
        required=False,
        metadata={"description": "Which session. Required to start working; optional to stop."},
    )
