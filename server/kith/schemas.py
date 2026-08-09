"""Marshmallow schemas — these define the request/response shapes and, via
APIFlask, are what the generated OpenAPI spec is built from.
"""

from __future__ import annotations

from apiflask import Schema
from apiflask.fields import Boolean, Dict, Integer, List, Nested, String
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
        metadata={
            "description": "Reasoning effort: '' (let the provider decide), 'none', 'minimal', 'low', "
            "'medium', 'high', 'xhigh' or 'max'",
            "example": "medium",
        }
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
