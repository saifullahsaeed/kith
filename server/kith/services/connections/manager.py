"""The lifecycle of a connection: load it, try it, choose a model, save it.

One object owns the whole arc, because the steps constrain each other and splitting
them is how you end up with a rejected key stored as the live configuration.

The rule this exists to enforce: **trying is not saving.** Onboarding probes a
candidate repeatedly while someone types, and none of that may touch the stored
config. Only ``adopt`` writes, and only after re-checking — so a stale browser tab
cannot save a connection that has since stopped working.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from kith import settings
from kith.domain.connection import Connection, ModelInfo, ProviderKind
from kith.infra.db import config_store
from kith.services.connections import providers
from kith.services.connections.providers import ProviderError

#: Marks that someone finished onboarding, so a deliberate choice is not re-asked.
#: A derived check alone cannot tell "never set up" from "set up locally, Ollama is
#: simply off right now", and re-running onboarding on the second case would be wrong.
ONBOARDED_KEY = "onboarded"

#: A model needs room to hold a multi-round turn. Below this he forgets his own
#: earlier steps inside a single tick.
USABLE_CONTEXT = 32_000

#: Suggestions aim higher than the bare minimum: a tick can run sixteen rounds, each
#: carrying tool output, so headroom is what keeps him coherent to the end of one.
SUGGESTION_CONTEXT = 65_536

#: How many starting points to pin above the full list.
SUGGESTION_COUNT = 3

#: Three price points, in dollars per million prompt tokens, and the cheapest model
#: that clears each. Bands rather than the three cheapest overall: a catalogue this
#: size makes the bottom three nearly identical micro-models, which is a poor set of
#: options and — measurably, on this project — poor advice. The cheap tier builds
#: things, then cannot check its own work against a spec. A ladder shows the real
#: range and lets someone spend deliberately.
PRICE_TIERS = (0.0, 0.30, 2.00)

#: Queued endpoints answer in minutes to hours. Fine for bulk work, useless for
#: something you are watching, so they never appear as a suggestion.
_ASYNC_SUFFIX = ":batch"

#: Above this, a model is cheap enough that its limits show up as lost work rather
#: than saved money. Set at the tier boundary so one number governs both.
BUDGET_CEILING = PRICE_TIERS[1]


@dataclass(frozen=True)
class ProbeResult:
    """What happened when we tried a connection."""

    reachable: bool
    detail: str = ""
    models: tuple[ModelInfo, ...] = ()
    suggested: tuple[str, ...] = ()

    def public(self) -> dict:
        return {
            "reachable": self.reachable,
            "detail": self.detail,
            "models": [model.public() for model in self.models],
            "suggested": list(self.suggested),
        }


@dataclass
class ConnectionManager:
    """Owns what is stored, what is being tried, and the move between them."""

    config_db: Path
    _providers = providers

    # -- reading what is stored --------------------------------------------- #

    def current(self) -> Connection:
        """The saved connection, with its provider inferred from the endpoint."""
        stored = config_store.load_settings(self.config_db)
        return Connection.infer(
            base_url=str(stored.get("base_url") or ""),
            api_key=str(stored.get("api_key") or ""),
            model=str(stored.get("model") or ""),
        )

    def is_onboarded(self) -> bool:
        """Has someone chosen, and is that choice still usable?

        Both halves matter. The flag alone would keep claiming success after a config
        was cleared; completeness alone would drag a local user back through setup
        every time they had not yet picked a model.
        """
        stored = config_store.load_settings(self.config_db)
        if ONBOARDED_KEY not in stored:
            return _configured_before_onboarding(stored) and self.current().is_complete
        return bool(stored[ONBOARDED_KEY]) and self.current().is_complete

    # -- trying a candidate ------------------------------------------------- #

    def candidate(self, kind: str, base_url: str = "", api_key: str = "", model: str = "") -> Connection:
        """Turn loose request fields into a Connection, filling in what we know.

        The one place raw input becomes a domain object, so routes never assemble a
        half-configured connection themselves. Config defaults land here rather than
        in the domain: OpenRouter's endpoint is fixed, and a local connection follows
        ``OLLAMA_HOST`` — which is the same address the chat path uses when the stored
        base URL is blank, so a probe tests what will actually run.
        """
        resolved = ProviderKind(kind)
        if resolved is ProviderKind.OLLAMA:
            return Connection.local(model=model.strip(), base_url=base_url.strip() or settings.OLLAMA_HOST)

        candidate = (
            Connection.openrouter(api_key=api_key.strip(), model=model.strip())
            if resolved is ProviderKind.OPENROUTER
            else Connection(
                kind=resolved,
                model=model.strip(),
                base_url=base_url.strip(),
                api_key=api_key.strip(),
            )
        )
        return candidate if candidate.api_key else self._with_stored_key(candidate)

    def _with_stored_key(self, candidate: Connection) -> Connection:
        """Reuse the saved key when the request omits one.

        A client is never sent the key back, so it has none to send. Without this,
        changing only the model would demand the key be pasted again — and the
        alternative, exposing it over the API to make round-tripping possible, is worse.

        Only when the endpoint is unchanged. That check is what stops this becoming a
        way to make Kith send an existing credential somewhere new.
        """
        saved = self.current()
        if saved.api_key and saved.kind is candidate.kind and saved.endpoint == candidate.endpoint:
            return replace(candidate, api_key=saved.api_key)
        return candidate

    def probe(self, candidate: Connection) -> ProbeResult:
        """Ask a provider whether it will talk to us, and what it offers.

        Never raises and never writes. A failure here is the normal case while
        someone is halfway through pasting a key, so it comes back as a sentence to
        display rather than an exception to handle.
        """
        provider = self._providers.for_kind(candidate.kind)
        if candidate.requires_key and not candidate.api_key and not provider.lists_without_key:
            return ProbeResult(reachable=False, detail="Paste an API key to continue.")
        try:
            found = provider.list_models(candidate)
        except ProviderError as failure:
            return ProbeResult(reachable=False, detail=str(failure))
        except Exception as exc:
            return ProbeResult(reachable=False, detail=f"{type(exc).__name__}: {exc}")

        return ProbeResult(
            reachable=True,
            models=tuple(found),
            suggested=tuple(self.suggest(candidate.kind, found)),
        )

    def suggest(self, kind: ProviderKind, models: list[ModelInfo]) -> list[str]:
        """A few sensible starting points, cheapest first.

        Computed from what the provider actually reports rather than a hardcoded list
        of slugs, which would rot within weeks. Nothing here claims to rank quality —
        a catalogue lists price, context and tool support, and not one of those is a
        measure of how well a model reasons. What it can honestly offer is a ladder:
        the cheapest model clearing the bar at each of three price points.
        """
        if kind is ProviderKind.OLLAMA:
            # Genuinely local and big enough first, then small, then Ollama's hosted
            # tags — which work but contradict the reason to pick this provider.
            ranked = sorted(
                models,
                key=lambda m: (
                    self._providers.is_cloud(m.id),
                    self._providers.is_small(m.id),
                    m.id,
                ),
            )
            return [model.id for model in ranked[:SUGGESTION_COUNT]]

        usable = [
            model
            for model in models
            # Unknown tool support passes: a generic endpoint never reports it, and
            # excluding those would leave the list empty.
            if model.supports_tools is not False
            and (model.context or 0) >= SUGGESTION_CONTEXT
            # Falsy covers both unpriced and dynamically priced: neither can be
            # ranked by cost, and a router's per-request price is not knowable.
            and model.prompt_per_mtok
            # Free tiers are heavily rate-limited — a poor first impression.
            and ":free" not in model.id
            and not model.id.endswith(_ASYNC_SUFFIX)
        ]
        usable.sort(key=lambda model: model.prompt_per_mtok or 0)

        ladder: list[str] = []
        for floor in PRICE_TIERS:
            pick = next(
                (m.id for m in usable if (m.prompt_per_mtok or 0) >= floor and m.id not in ladder),
                None,
            )
            if pick:
                ladder.append(pick)
        return ladder[:SUGGESTION_COUNT]

    def concerns(self, connection: Connection, models: tuple[ModelInfo, ...]) -> list[str]:
        """Things worth saying about a specific choice, without blocking it."""
        notes: list[str] = []
        chosen = next((m for m in models if m.id == connection.model), None)

        if connection.is_local and self._providers.is_small(connection.model):
            notes.append(
                "Small local models struggle with multi-step work — expect him to lose track on longer tasks."
            )
        if connection.is_local and self._providers.is_cloud(connection.model):
            notes.append(
                "This is one of Ollama's hosted models, so requests do leave your "
                "machine and need an Ollama account — unlike the rest of this option."
            )
        if chosen and chosen.supports_tools is False:
            notes.append(
                "This model can't call tools, so he could only talk — no files, no "
                "search, and no memory of his own."
            )
        if (
            chosen
            and not connection.is_local
            and chosen.prompt_per_mtok is not None
            and 0 < chosen.prompt_per_mtok < BUDGET_CEILING
        ):
            notes.append(
                "At this price he'll happily build things, but he tends not to catch "
                "his own mistakes — expect to hand work back to him."
            )
        if chosen and chosen.context is not None and chosen.context < USABLE_CONTEXT:
            notes.append(
                "A short context window means he'll forget his own earlier steps within a single turn."
            )
        if not connection.supports_web_plugin:
            notes.append(
                "Search won't run through this provider — he'll need a SearXNG instance to search the web."
            )
        return notes

    # -- committing --------------------------------------------------------- #

    def adopt(self, candidate: Connection) -> tuple[Connection, list[str]]:
        """Save a connection, after checking it still works.

        Raises ValueError with something readable if it does not, so the caller can
        hand that straight to the user.

        Clearing the endpoint and key for a local connection is deliberate: leaving a
        previous cloud setup in place would let it keep quietly taking precedence,
        because that is how ``Connection.infer`` reads the stored values back.
        """
        for problem in candidate.problems():
            raise ValueError(problem)

        result = self.probe(candidate)
        if not result.reachable:
            raise ValueError(result.detail)
        if result.models and not any(m.id == candidate.model for m in result.models):
            raise ValueError(f"{candidate.model} isn't offered by that provider.")

        config_store.update_settings(
            self.config_db,
            {
                "model": candidate.model,
                "base_url": "" if candidate.is_local else candidate.endpoint,
                "api_key": "" if candidate.is_local else candidate.api_key,
                ONBOARDED_KEY: True,
            },
        )
        return self.current(), self.concerns(candidate, result.models)


def _configured_before_onboarding(stored: dict) -> bool:
    """Did someone set this up by hand, back when that was the only way?

    Onboarding writes a flag; installs that predate it have none, and showing them a
    first-run wizard over a working setup would be a regression. But the config
    database is *seeded* with a default model, so "a model is set" proves nothing on
    its own — a fresh install would look configured and skip setup entirely.

    A deliberate choice therefore means an API key, or a model that isn't the seed.
    """
    if stored.get("api_key"):
        return True
    return str(stored.get("model") or "") != config_store.DEFAULT_SETTINGS["model"]
