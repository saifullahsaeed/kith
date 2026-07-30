"""Where Kith thinks.

A single type for the thing that was previously four loose values passed around
together — endpoint, key, model, and an implied provider — with each caller deciding
for itself what they meant. Three separate places had grown their own answer to
"is this OpenRouter?", each spelled slightly differently, and nothing owned the
question of whether a connection was usable at all.

Pure by design: no network, no database, no config. A ``Connection`` can be built,
compared, and reasoned about in a test without anything running. Talking to a
provider is the job of ``services.connections``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum


class ProviderKind(StrEnum):
    """The three genuinely different ways to give him a brain.

    Not cosmetic labels — they differ in what they need and what they unlock. The
    string values are the API's vocabulary, so they are stable.
    """

    OPENROUTER = "openrouter"
    OPENAI_COMPATIBLE = "openai"
    OLLAMA = "ollama"


#: Where Ollama listens unless told otherwise. Here rather than in settings because
#: it is part of what "a local connection" *means*, not something an operator tunes.
OLLAMA_DEFAULT_URL = "http://127.0.0.1:11434"

#: Recognising OpenRouter by host. It is the only provider that accepts the `web`
#: plugin and the `provider`/`usage` extensions, so this one check gates real
#: behaviour — which is exactly why it belongs in one place.
_OPENROUTER_HOST = "openrouter.ai"
_OPENROUTER_URL = "https://openrouter.ai/api/v1"


@dataclass(frozen=True)
class ModelInfo:
    """One model a provider offers, in the shape a picker needs.

    Fields are ``None`` when the provider does not say, never a guessed default: a
    missing price must read as "unknown", not as free, and unknown tool support must
    not look like a refusal.
    """

    id: str
    name: str = ""
    prompt_per_mtok: float | None = None
    completion_per_mtok: float | None = None
    context: int | None = None
    supports_tools: bool | None = None

    @property
    def label(self) -> str:
        return self.name or self.id

    @property
    def is_free(self) -> bool:
        """Genuinely free, as opposed to unpriced."""
        return self.prompt_per_mtok == 0 and self.completion_per_mtok == 0

    def public(self) -> dict:
        return {
            "id": self.id,
            "name": self.label,
            "promptPerMTok": self.prompt_per_mtok,
            "completionPerMTok": self.completion_per_mtok,
            "context": self.context,
            "supportsTools": self.supports_tools,
        }


@dataclass(frozen=True)
class Connection:
    """A provider, an endpoint, a credential and a model — as one thing.

    Immutable: changing a connection produces a new one, so a half-edited candidate
    can never be mistaken for the live configuration.
    """

    kind: ProviderKind
    model: str = ""
    base_url: str = ""
    api_key: str = ""

    # -- construction ------------------------------------------------------- #

    @classmethod
    def local(cls, model: str = "", base_url: str = "") -> Connection:
        return cls(kind=ProviderKind.OLLAMA, model=model, base_url=base_url)

    @classmethod
    def openrouter(cls, api_key: str, model: str = "") -> Connection:
        return cls(kind=ProviderKind.OPENROUTER, model=model, base_url=_OPENROUTER_URL, api_key=api_key)

    @classmethod
    def infer(cls, base_url: str, api_key: str, model: str) -> Connection:
        """Work out which provider a stored configuration describes.

        The config database predates this type and stores no provider name, so the
        kind is derived: a key and an endpoint mean cloud, and the host decides
        whether that cloud is OpenRouter. Anything else is local.
        """
        if base_url and api_key:
            kind = ProviderKind.OPENROUTER if _OPENROUTER_HOST in base_url else ProviderKind.OPENAI_COMPATIBLE
            return cls(kind=kind, model=model, base_url=base_url, api_key=api_key)
        return cls.local(model=model, base_url=base_url)

    def with_model(self, model: str) -> Connection:
        return replace(self, model=model)

    # -- what this connection is -------------------------------------------- #

    @property
    def is_local(self) -> bool:
        return self.kind is ProviderKind.OLLAMA

    @property
    def requires_key(self) -> bool:
        """Only a local model needs no credential."""
        return not self.is_local

    @property
    def endpoint(self) -> str:
        """The address to actually call, with the local default filled in."""
        if self.base_url:
            return self.base_url.rstrip("/")
        return OLLAMA_DEFAULT_URL if self.is_local else ""

    @property
    def supports_web_plugin(self) -> bool:
        """Can he search through the provider itself?

        OpenRouter's ``web`` plugin is a vendor extension. Other OpenAI-compatible
        hosts do not ignore it — they reject the whole request — so this decides both
        whether search works without SearXNG and which parameters are safe to send.
        """
        return self.kind is ProviderKind.OPENROUTER

    @property
    def is_complete(self) -> bool:
        """Enough here for him to think, ignoring whether the provider is reachable."""
        return not self.problems()

    def problems(self) -> list[str]:
        """What is structurally missing, in words worth showing someone.

        Structure only — no network. "Unreachable" is a different question with a
        different answer, and conflating them makes a typo look like an outage.
        """
        missing = []
        if not self.model:
            missing.append("Choose a model.")
        if self.requires_key and not self.base_url:
            missing.append("This provider needs a base URL.")
        if self.requires_key and not self.api_key:
            missing.append("This provider needs an API key.")
        return missing

    # -- crossing a boundary ------------------------------------------------ #

    def public(self) -> dict:
        """Safe to send to a client: says whether a key is set, never what it is."""
        return {
            "kind": str(self.kind),
            "model": self.model or None,
            "baseUrl": self.base_url or None,
            "apiKeySet": bool(self.api_key),
            "requiresKey": self.requires_key,
            "supportsWebSearch": self.supports_web_plugin,
            "isComplete": self.is_complete,
        }

    def __repr__(self) -> str:
        """Never print the key — these objects end up in logs and tracebacks."""
        held = "key set" if self.api_key else "no key"
        return f"Connection({self.kind}, model={self.model!r}, {self.endpoint or 'no endpoint'}, {held})"
