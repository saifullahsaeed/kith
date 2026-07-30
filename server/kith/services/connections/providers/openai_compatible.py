"""Any OpenAI-compatible endpoint: OpenAI, NVIDIA NIM, DeepSeek, Groq, Together…"""

from __future__ import annotations

from typing import ClassVar

from kith.domain.connection import Connection, ModelInfo, ProviderKind
from kith.services.connections.providers.base import Provider, ProviderError


class OpenAICompatibleProvider(Provider):
    kind: ClassVar[ProviderKind] = ProviderKind.OPENAI_COMPATIBLE
    label: ClassVar[str] = "OpenAI-compatible"
    blurb: ClassVar[str] = "Your own endpoint — OpenAI, NVIDIA NIM, DeepSeek, Groq."
    requires: ClassVar[str] = "A base URL and an API key"
    tradeoff: ClassVar[str] = (
        "Use a provider you already pay for. Web search won't work through it, so he "
        "needs a SearXNG instance to search at all."
    )

    def list_models(self, connection: Connection) -> list[ModelInfo]:
        """``/models`` on a generic endpoint: identifiers and nothing else.

        No pricing and no capability flags exist here, so every optional field stays
        None. That is deliberate — the picker then shows "unknown" instead of implying
        a model is free or that its tool support has been checked.
        """
        if not connection.endpoint:
            raise ProviderError("Enter the base URL, e.g. https://api.openai.com/v1")
        payload = self._get_json(f"{connection.endpoint}/models", connection.api_key)

        entries = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(entries, list):
            raise ProviderError("That endpoint answered, but not with a list of models.")

        models = []
        for entry in entries:
            identifier = entry.get("id") if isinstance(entry, dict) else str(entry)
            if identifier:
                models.append(ModelInfo(id=identifier, name=identifier))
        if not models:
            raise ProviderError("That endpoint offers no models on this key.")
        return models
