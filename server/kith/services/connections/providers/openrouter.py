"""OpenRouter: one key, hundreds of models, and the only one where search works."""

from __future__ import annotations

from typing import ClassVar

from kith.domain.connection import Connection, ModelInfo, ProviderKind
from kith.services.connections.providers.base import Provider

_CATALOGUE = "https://openrouter.ai/api/v1/models"

#: The only endpoint that actually authenticates. ``/models`` is public and answers
#: 200 to any nonsense in the header, so it cannot be used to check a key.
_KEY_INFO = "https://openrouter.ai/api/v1/key"


class OpenRouterProvider(Provider):
    kind: ClassVar[ProviderKind] = ProviderKind.OPENROUTER
    label: ClassVar[str] = "OpenRouter"
    blurb: ClassVar[str] = "One key, hundreds of models. Web search included."
    requires: ClassVar[str] = "An API key from openrouter.ai"
    tradeoff: ClassVar[str] = (
        "The simplest way to give him everything he can do. Search runs through the "
        "same key at about half a cent a search, so he needs nothing else installed."
    )
    signup_url: ClassVar[str] = "https://openrouter.ai/keys"
    #: The catalogue is public, so the picker can be filled before a key exists.
    lists_without_key: ClassVar[bool] = True

    def list_models(self, connection: Connection) -> list[ModelInfo]:
        """The catalogue, with prices and — crucially — who can call tools.

        OpenRouter publishes ``supported_parameters`` per model, and a meaningful
        share of them cannot call tools at all. Kith without tool calling can only
        talk: no files, no search, no memory. Carrying that flag through is what
        lets onboarding refuse to offer a confidently broken setup.
        """
        payload = self._get_json(_CATALOGUE, connection.api_key)
        models = []
        for entry in payload.get("data") or []:
            identifier = entry.get("id")
            if not identifier:
                continue
            pricing = entry.get("pricing") or {}
            models.append(
                ModelInfo(
                    id=identifier,
                    name=entry.get("name") or identifier,
                    prompt_per_mtok=_per_million(pricing.get("prompt")),
                    completion_per_mtok=_per_million(pricing.get("completion")),
                    context=entry.get("context_length"),
                    supports_tools="tools" in (entry.get("supported_parameters") or []),
                )
            )
        return models

    def verify_key(self, connection: Connection) -> str:
        """Ask about the key itself, and report what it can spend.

        ``_get_json`` turns the 401 into "That key was rejected", which is the whole
        point of the call. The credit line is the reassurance: an onboarding step that
        says "connected" without evidence is indistinguishable from one that is lying.
        """
        data = self._get_json(_KEY_INFO, connection.api_key).get("data") or {}
        spent = data.get("usage")
        allowance = data.get("limit")
        remaining = data.get("limit_remaining")

        if allowance is None:
            note = f"${float(spent):.2f} used so far" if isinstance(spent, int | float) else "pay as you go"
        elif isinstance(remaining, int | float):
            note = f"${float(remaining):.2f} of credit left"
        else:
            note = f"${float(allowance):.2f} limit"

        if data.get("is_free_tier"):
            # Worth saying: the free tier is rate-limited hard enough that he will
            # stall mid-task, which looks like a bug in him rather than a quota.
            return f"Free tier — {note}. Expect rate limits on long tasks."
        return note


def _per_million(raw: object) -> float | None:
    """Priced per token as a string; people read dollars per million.

    A negative price is OpenRouter's way of saying "it depends" — the routing models
    (``openrouter/auto`` and friends) pick a backend per request, so the cost is not
    knowable up front. Reported as unknown rather than passed through, because
    ``-1`` per token becomes -$1,000,000/Mtok and sorts to the top of any
    cheapest-first list.
    """
    try:
        dollars = float(raw) * 1_000_000
    except (TypeError, ValueError):
        return None
    return round(dollars, 4) if dollars >= 0 else None
