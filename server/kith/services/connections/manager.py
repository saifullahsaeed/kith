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
from enum import StrEnum
from pathlib import Path

from kith.config import ollama_host
from kith.domain.connection import Connection, ModelInfo, Pick, ProviderKind, Tier
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

#: The agentic score a model has to clear before it is offered as good value.
#:
#: Not a round number picked for looking principled. Across OpenRouter's tool-capable
#: catalogue the agentic index runs 0 to 55 with a median near 21, and the models that
#: measurably finish multi-step work start around here. Below it they build things
#: enthusiastically and then cannot check their own work — which is exactly what this
#: project watched happen, at some expense, before the score was available to consult.
AGENTIC_FLOOR = 30.0

#: Above this, a model is cheap enough that its limits show up as lost work rather
#: than saved money. Used to warn, never to block.
BUDGET_CEILING = 0.30


class KeyState(StrEnum):
    """Whether the credential is good — a different question from reachability.

    They have to be separate because OpenRouter's catalogue is public: it answers
    happily with no key at all, which is useful (the model picker can be filled
    while someone is still deciding) and dangerous (a listing proves nothing about
    the key). Conflating the two is how a rejected key gets saved.
    """

    NOT_REQUIRED = "not_required"
    #: Reached the provider, but no key has been offered yet.
    MISSING = "missing"
    VALID = "valid"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ProbeResult:
    """What happened when we tried a connection."""

    reachable: bool
    detail: str = ""
    models: tuple[ModelInfo, ...] = ()
    #: Recommendations, each with the reason for it. Ordered strongest-first.
    suggested: tuple[Pick, ...] = ()
    key_state: KeyState = KeyState.NOT_REQUIRED
    #: Something worth showing about the credential: remaining credit, or the
    #: provider's own words for why it said no.
    key_detail: str = ""

    @property
    def usable(self) -> bool:
        """Reached, and holding a credential it will accept."""
        return self.reachable and self.key_state in (KeyState.VALID, KeyState.NOT_REQUIRED)

    def public(self) -> dict:
        return {
            "reachable": self.reachable,
            "usable": self.usable,
            "detail": self.detail,
            "models": [model.public() for model in self.models],
            "suggested": [pick.public() for pick in self.suggested],
            "keyState": str(self.key_state),
            "keyDetail": self.key_detail,
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
            return Connection.local(model=model.strip(), base_url=base_url.strip() or ollama_host())

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
        missing_key = candidate.requires_key and not candidate.api_key
        if missing_key and not provider.lists_without_key:
            return ProbeResult(
                reachable=False,
                detail="Paste an API key to continue.",
                key_state=KeyState.MISSING,
            )
        try:
            found = provider.list_models(candidate)
        except ProviderError as failure:
            return ProbeResult(reachable=False, detail=str(failure), key_state=self._key_state(candidate))
        except Exception as exc:
            return ProbeResult(reachable=False, detail=f"{type(exc).__name__}: {exc}")

        # Listing worked, which for a public catalogue says nothing about the key.
        key_state, key_detail = KeyState.NOT_REQUIRED, ""
        if candidate.requires_key:
            if missing_key:
                key_state = KeyState.MISSING
            else:
                try:
                    key_detail = provider.verify_key(candidate)
                    key_state = KeyState.VALID
                except ProviderError as rejected:
                    key_state, key_detail = KeyState.REJECTED, str(rejected)
                except Exception as exc:
                    key_state, key_detail = KeyState.REJECTED, f"{type(exc).__name__}: {exc}"

        return ProbeResult(
            reachable=True,
            models=tuple(found),
            suggested=tuple(self.suggest(candidate.kind, found)),
            key_state=key_state,
            key_detail=key_detail,
        )

    @staticmethod
    def _key_state(candidate: Connection) -> KeyState:
        """The credential's status when listing itself failed, and told us nothing."""
        if not candidate.requires_key:
            return KeyState.NOT_REQUIRED
        return KeyState.MISSING if not candidate.api_key else KeyState.REJECTED

    def suggest(self, kind: ProviderKind, models: list[ModelInfo]) -> list[Pick]:
        """Three recommendations, each for a different reason.

        Not a ranking of one axis, because the choice isn't one: someone picking a
        model is choosing between the strongest available, the best per dollar, and one
        whose weights are published and so cannot be withdrawn. Three points on a price
        ladder — what this used to return — made all three look like the same decision
        taken at different budgets.

        Every pick carries its evidence, so it can be checked rather than trusted.
        Where a provider publishes no measurements, that is said plainly instead of
        being papered over with price.
        """
        if kind is ProviderKind.OLLAMA:
            return self._local_picks(models)

        candidates = [m for m in models if m.is_recommendable and (m.context or 0) >= SUGGESTION_CONTEXT]
        if any(m.agentic_index is not None for m in candidates):
            return self._measured_picks(candidates)
        return self._price_picks(candidates)

    def _measured_picks(self, models: list[ModelInfo]) -> list[Pick]:
        """The good case: the provider publishes agentic scores, so this is evidence."""
        scored = [m for m in models if m.agentic_index is not None]
        by_ability = sorted(scored, key=lambda m: -(m.agentic_index or 0))
        taken: set[str] = set()
        picks: list[Pick] = []

        def take(candidates: list[ModelInfo], tier: Tier, headline: str, reason) -> None:
            """Fill a tier with the best candidate not already recommended.

            Taking the *next* one rather than skipping the tier matters: the strongest
            model in the catalogue is often also the strongest with published weights,
            and a tier that silently disappeared would leave someone who cares about
            open weights with nothing to choose.
            """
            model = next((m for m in candidates if m.id not in taken), None)
            if model is None:
                return
            taken.add(model.id)
            picks.append(Pick(tier=tier, model_id=model.id, headline=headline, reason=reason(model)))

        take(
            by_ability,
            Tier.FRONTIER,
            "The strongest",
            lambda m: (
                f"Top agentic score here ({_score(m)}) — the best at seeing a long "
                "job through, and the most expensive way to run him."
            ),
        )

        # Cheapest that still clears the bar, rather than cheapest outright: below the
        # floor the saving is undone by work he has to be given back.
        affordable = sorted(
            (m for m in scored if (m.agentic_index or 0) >= AGENTIC_FLOOR and m.prompt_per_mtok),
            key=lambda m: m.prompt_per_mtok or 0,
        )
        take(
            affordable,
            Tier.VALUE,
            "Best value",
            lambda m: (
                f"The cheapest model that still scores well on agentic work "
                f"({_score(m)}). Where most people should start."
            ),
        )

        take(
            [m for m in by_ability if m.open_weights],
            Tier.OPEN,
            "Open weights",
            lambda m: (
                f"The strongest model here whose weights are published "
                f"({_score(m)}), so it can outlive whoever is serving it today."
            ),
        )
        return picks

    def _price_picks(self, models: list[ModelInfo]) -> list[Pick]:
        """No published measurements, so say what we do know and no more.

        A generic OpenAI-compatible endpoint reports ids and little else. Inventing a
        ranking from a name would be worse than admitting there isn't one.
        """
        priced = sorted((m for m in models if m.prompt_per_mtok), key=lambda m: m.prompt_per_mtok or 0)
        if not priced:
            return [
                Pick(
                    tier=Tier.VALUE,
                    model_id=model.id,
                    headline="Available",
                    reason="This endpoint doesn't publish prices or benchmarks, so this is "
                    "simply what it offers.",
                )
                for model in models[:SUGGESTION_COUNT]
            ]
        cheapest, dearest = priced[0], priced[-1]
        picks = [
            Pick(
                tier=Tier.VALUE,
                model_id=cheapest.id,
                headline="Cheapest",
                reason="The lowest price here. This endpoint publishes no benchmark "
                "scores, so nothing is claimed about how well it works.",
            )
        ]
        if dearest.id != cheapest.id:
            picks.append(
                Pick(
                    tier=Tier.FRONTIER,
                    model_id=dearest.id,
                    headline="Most expensive",
                    reason="Usually the flagship, though price is a poor proxy and this "
                    "endpoint offers nothing better to go on.",
                )
            )
        return picks

    def _local_picks(self, models: list[ModelInfo]) -> list[Pick]:
        """What is already pulled, best-suited first.

        Ollama publishes neither context nor benchmarks, so parameter count is the only
        signal there is — and it says so rather than dressing size up as quality.
        """
        ranked = sorted(
            models,
            key=lambda m: (self._providers.is_cloud(m.id), self._providers.is_small(m.id), m.id),
        )
        picks = []
        for model in ranked[:SUGGESTION_COUNT]:
            if self._providers.is_cloud(model.id):
                headline, reason = (
                    "Hosted by Ollama",
                    "Works, but it runs on Ollama's servers rather than your machine.",
                )
            elif self._providers.is_small(model.id):
                headline, reason = (
                    "Small and quick",
                    "Fast and light, but small models lose track on long, multi-step work.",
                )
            else:
                headline, reason = (
                    "Runs on your machine",
                    "Free, private, and big enough to be worth pointing at real work.",
                )
            picks.append(Pick(tier=Tier.VALUE, model_id=model.id, headline=headline, reason=reason))
        return picks

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
        # Reachable is not enough. A public catalogue answers without a key, so this
        # is the check that stops an invalid one being stored to fail later, during
        # his first message, where it looks like something else entirely.
        if not result.usable:
            raise ValueError(result.key_detail or "That key was rejected.")
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


def _score(model: ModelInfo | None) -> str:
    """An agentic index as prose. One decimal: the source publishes no more."""
    if model is None or model.agentic_index is None:
        return "unscored"
    return f"{model.agentic_index:.1f}"


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
