"""Marshmallow schemas: the request/response shapes, and — via APIFlask — what the generated
OpenAPI spec at ``/openapi.json`` is built from.

Every docstring here becomes a description in that spec, so they are written for someone reading
the API docs rather than for someone reading this file.
"""

from __future__ import annotations

from apiflask import Schema
from apiflask.fields import Boolean, Dict, Integer, List, Nested, String
from marshmallow import EXCLUDE

from kith.infra.db.config_store import DEFAULT_SETTINGS

# The spec's examples are the real built-in defaults rather than a second copy of them, so the
# published docs cannot drift from what an unconfigured Kith actually starts with.
_EG_MODEL = DEFAULT_SETTINGS["model"]
_EG_NUM_CTX = DEFAULT_SETTINGS["num_ctx"]
_EG_NUM_PREDICT = DEFAULT_SETTINGS["num_predict"]


class ConfigSchema(Schema):
    """Chat parameters: the ``/config`` response, and optional per-request overrides on
    ``/chat`` where every field is optional. Anything omitted keeps the server's current value."""

    class Meta:
        unknown = EXCLUDE

    model = String(metadata={"description": "Model tag (Ollama) or model id (cloud)", "example": _EG_MODEL})
    numCtx = Integer(metadata={"description": "Context window in tokens", "example": _EG_NUM_CTX})
    numPredict = Integer(
        metadata={"description": "Max output tokens (-1 = unlimited)", "example": _EG_NUM_PREDICT}
    )
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
    """One turn of the conversation, as the caller sends it."""

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
    canvas = List(
        Dict(),
        required=False,
        metadata={
            "description": "What the person has set on a canvas he drew, as of sending this "
            "message: [{title, values: {name: string|number|bool}}]. Reported by the sealed "
            "frame itself, so treated as untrusted and rendered as a short read-only note."
        },
    )


class ChatRequestSchema(Schema):
    """A chat turn. The response is an NDJSON stream, not JSON — reasoning and answer arrive as
    separate frames, so this shape describes only what goes up."""

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
    projectId = Integer(
        required=False,
        allow_none=True,
        metadata={
            "description": "The project this conversation is in. Sent with the *first* turn of "
            "a chat started inside a project, because there is no conversation id to bind "
            "against until this request creates one — and the turn that most needs to know "
            "which project it is in is that first one. Ignored once the conversation is bound: "
            "a conversation keeps the project it picked.",
        },
    )


class HealthSchema(Schema):
    """Whether the backend is up, and whether the model it is configured for answered."""

    ok = Boolean(metadata={"description": "The backend is up"})
    ollamaReachable = Boolean(metadata={"description": "Ollama answered a probe"})


class ReplySchema(Schema):
    """One question's answer. All three may be present: they picked an option and typed a
    caveat alongside it, which is the commonest real answer to a multiple-choice question."""

    class Meta:
        unknown = EXCLUDE

    chosen = List(String(), metadata={"description": "Labels of the options picked, in order."})
    text = String(metadata={"description": "What they typed instead of, or alongside, an option."})
    skipped = Boolean(metadata={"description": "They would rather he decided."})


class AnswerSchema(Schema):
    """The answers to one `ask`, in the order the questions were put."""

    class Meta:
        unknown = EXCLUDE

    answers = List(Nested(ReplySchema), required=True)
