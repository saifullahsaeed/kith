"""The providers Kith can think through, and how to find one.

A registry rather than a chain of ``if kind ==``: adding a provider means adding a
module and one line here, and the onboarding cards come from the registry itself, so
the UI needs no corresponding change.
"""

from __future__ import annotations

from kith.domain.connection import ProviderKind
from kith.services.connections.providers.base import Provider, ProviderError
from kith.services.connections.providers.ollama import OllamaProvider, is_cloud, is_small
from kith.services.connections.providers.openai_compatible import OpenAICompatibleProvider
from kith.services.connections.providers.openrouter import OpenRouterProvider

__all__ = ["Provider", "ProviderError", "cards", "for_kind", "is_cloud", "is_small"]

# Order is the order the choice cards appear in. OpenRouter first because it is the
# one that needs nothing else installed and unlocks everything he can do.
_PROVIDERS: tuple[Provider, ...] = (
    OpenRouterProvider(),
    OpenAICompatibleProvider(),
    OllamaProvider(),
)

_BY_KIND = {provider.kind: provider for provider in _PROVIDERS}


def for_kind(kind: ProviderKind | str) -> Provider:
    """The provider for a kind, or a ValueError naming the valid ones."""
    try:
        resolved = ProviderKind(kind)
    except ValueError:
        raise ValueError(f"unknown provider {kind!r}. Valid: {', '.join(str(k) for k in _BY_KIND)}") from None
    return _BY_KIND[resolved]


def cards() -> list[dict]:
    """The choice cards, in display order."""
    return [provider.card() for provider in _PROVIDERS]
