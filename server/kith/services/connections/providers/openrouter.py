"""OpenRouter, through its own SDK.

Everything else here speaks plain OpenAI-compatible HTTP, and OpenRouter does too —
but the parts worth having are the parts that aren't in that surface. Their SDK
exposes them, generated from their OpenAPI spec so it tracks the API:

* the catalogue carries **Artificial Analysis benchmark scores**, including an
  ``agentic_index`` — how well a model sustains multi-step tool use. That is the whole
  of what Kith does, and it is the one thing price does not predict. It is what makes
  the model picker able to recommend by measured ability rather than by being cheap.
* ``hugging_face_id`` says whether the weights are published, which is an exact answer
  to "is this open source" rather than a guess from the name.
* ``expiration_date`` flags models that will stop working on a date nobody chose.
* filtering and sorting happen **server-side** — ``sort=agentic-high-to-low``,
  ``supported_parameters=tools`` — so we are not paging 400 models to sort them here.
* the credits endpoint gives an account balance, which is a far better thing to show
  someone than the usage figure on their key.

The catalogue is public: constructing the client without a key works, which is what
lets the picker fill while someone is still deciding. Passing an empty string does
*not* — the SDK sends a malformed header — hence ``api_key or None`` throughout.
"""

from __future__ import annotations

from typing import Any, ClassVar

from openrouter import OpenRouter
from openrouter import errors as sdk_errors

from kith.domain.connection import Connection, ModelInfo, ProviderKind
from kith.services.connections.providers.base import Provider, ProviderError

#: Ask the API to rank by agentic ability rather than doing it here. The ordering is
#: also the fallback ordering of the full list, so the picker's first page is useful
#: even before anyone searches.
_CATALOGUE_SORT = "agentic-high-to-low"

#: He reads and writes text. Image and audio models would only be noise in a picker
#: that already runs to hundreds of rows.
_TEXT_ONLY = "text"

#: Dropped from the catalogue entirely, not merely left out of the recommendations.
#: These are the 28 ``:batch`` twins of ordinary models, and a normal chat call to one
#: answers::
#:
#:     404 This model is only available through the Batch API.
#:
#: They are also cheaper than their twins, so they look like a bargain right up until
#: nothing works. Every one duplicates a model that is already listed, so nothing is
#: lost by hiding them — unlike ``:free`` or preview variants, which do work and stay
#: searchable.
_BATCH_ONLY_SUFFIX = ":batch"


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
        """The catalogue, ranked by agentic ability, with the benchmark scores kept.

        ``supported_parameters`` is deliberately *not* filtered server-side. A model
        that cannot call tools is useless to Kith, but hiding it entirely turns
        "this one can't do what you need" into "this one doesn't exist", and someone
        looking for a model they know the name of deserves to find it and be told why.
        """
        with self._client(connection) as client:
            page = self._call(client.models.list, sort=_CATALOGUE_SORT, output_modalities=_TEXT_ONLY)
        return [
            _to_model_info(entry)
            for entry in page.result.data
            if not str(entry.id).endswith(_BATCH_ONLY_SUFFIX)
        ]

    def verify_key(self, connection: Connection) -> str:
        """Check the credential, and report what the account can spend.

        ``/models`` is public and answers 200 to a key made of nonsense, so a listing
        that succeeded proves nothing. This call is the one that authenticates.

        The balance comes from the credits endpoint rather than the key's own usage
        counter: "$351.34 left" is what someone wants to know, and "$0.47 used on this
        key" is not the same question.
        """
        with self._client(connection) as client:
            credits = self._call(client.credits.get_credits).data
            free_tier = self._is_free_tier(client)

        remaining = float(credits.total_credits or 0) - float(credits.total_usage or 0)
        note = f"${remaining:,.2f} of credit left"
        if free_tier:
            # Worth saying: the free tier is rate-limited hard enough that he stalls
            # mid-task, which reads as a bug in him rather than a quota.
            return f"Free tier — {note}. Expect rate limits on long tasks."
        return note

    # -- plumbing ------------------------------------------------------------ #

    @staticmethod
    def _client(connection: Connection) -> OpenRouter:
        """A client for this connection. ``None`` rather than ``""`` when keyless:
        the SDK would otherwise send ``Bearer `` and httpx rejects it locally."""
        return OpenRouter(
            api_key=connection.api_key or None,
            # Identifies Kith in OpenRouter's app rankings, and is how they attribute
            # traffic. Costs nothing and is the polite thing to send.
            http_referer="https://github.com/kith",
            x_open_router_title="Kith",
        )

    @staticmethod
    def _call(operation, **kwargs) -> Any:
        """Run an SDK call, turning its exceptions into a sentence worth reading.

        The SDK raises a distinct class per status. Mapping them here keeps every
        provider's failures the same shape, so the UI has one thing to display.
        """
        try:
            return operation(**kwargs)
        except sdk_errors.UnauthorizedResponseError as rejected:
            raise ProviderError(f"That key was rejected. {_message(rejected)}") from None
        except sdk_errors.ForbiddenResponseError as refused:
            raise ProviderError(f"That key isn't allowed to do this. {_message(refused)}") from None
        except sdk_errors.PaymentRequiredResponseError as broke:
            raise ProviderError(
                f"The account is out of credit. {_message(broke)} Top up at openrouter.ai/credits."
            ) from None
        except sdk_errors.TooManyRequestsResponseError as limited:
            raise ProviderError(f"Rate limited by OpenRouter. {_message(limited)}") from None
        except sdk_errors.OpenRouterDefaultError as failed:
            raise ProviderError(f"OpenRouter said no. {_message(failed)}") from None
        except Exception as exc:
            # Anything left is a transport or validation problem — usually no network.
            raise ProviderError(f"Couldn't reach OpenRouter. {type(exc).__name__}: {exc}") from exc

    @staticmethod
    def _is_free_tier(client: OpenRouter) -> bool:
        """A nice-to-have, so its failure must not fail the check that matters."""
        try:
            return bool(client.api_keys.get_current_key_metadata().data.is_free_tier)
        except Exception:
            return False


def _to_model_info(entry: Any) -> ModelInfo:
    """One catalogue entry, flattened to what a picker shows."""
    pricing = entry.pricing
    scores = _artificial_analysis(entry)
    return ModelInfo(
        id=entry.id,
        name=entry.name or entry.id,
        prompt_per_mtok=_per_million(getattr(pricing, "prompt", None)),
        completion_per_mtok=_per_million(getattr(pricing, "completion", None)),
        # Absent on the providers that cache automatically and charge nothing extra for
        # it, and on the ones that do not cache at all — so None means "not quoted",
        # which the picker shows as nothing rather than as free.
        cache_read_per_mtok=_per_million(getattr(pricing, "input_cache_read", None)),
        cache_write_per_mtok=_per_million(getattr(pricing, "input_cache_write", None)),
        context=entry.context_length,
        supports_tools="tools" in (entry.supported_parameters or []),
        agentic_index=_number(scores.get("agentic_index")),
        coding_index=_number(scores.get("coding_index")),
        # Published weights. An exact answer, where guessing from the vendor name
        # would be wrong in both directions.
        open_weights=bool(entry.hugging_face_id),
        retires_on=str(entry.expiration_date) if entry.expiration_date else None,
    )


def _artificial_analysis(entry: Any) -> dict:
    """The benchmark block, which is absent for most of the catalogue.

    Tolerates both a model object and a plain dict: the SDK returns the former, and
    the field is loosely typed enough that it has been observed as the latter.
    """
    block = getattr(entry, "benchmarks", None)
    if block is None:
        return {}
    scores = (
        block.get("artificial_analysis")
        if isinstance(block, dict)
        else getattr(block, "artificial_analysis", None)
    )
    if scores is None:
        return {}
    return scores if isinstance(scores, dict) else scores.model_dump()


def _number(raw: object) -> float | None:
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _per_million(raw: object) -> float | None:
    """Priced per token as a string; people read dollars per million.

    A negative price is OpenRouter's way of saying "it depends" — the routing models
    (``openrouter/auto`` and friends) pick a backend per request, so the cost is not
    knowable up front. Reported as unknown rather than passed through, because ``-1``
    per token becomes -$1,000,000/Mtok and sorts to the top of any cheapest-first list.

    This is the base tier only. Some models price higher above a prompt-length
    threshold; the picker says "from" rather than implying one flat rate.
    """
    try:
        dollars = float(raw) * 1_000_000  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return round(dollars, 4) if dollars >= 0 else None


def _message(error: Exception) -> str:
    """The provider's own words, which beat anything we could write about them."""
    body = getattr(error, "data", None) or getattr(error, "body", None)
    detail = getattr(body, "message", None) if body is not None else None
    return str(detail or error)[:200].strip()
