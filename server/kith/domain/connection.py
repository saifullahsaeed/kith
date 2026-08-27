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


def is_openrouter(base_url: str) -> bool:
    """Is this endpoint OpenRouter?

    The check the module docstring above is about. It gates the `web` plugin and the
    `provider`/`usage` extensions, and other OpenAI-compatible hosts do not ignore those
    keys — they reject the whole request — so getting it wrong is a 400, not a no-op.

    It lived in `llm/openai_compat.py` and was called from `infra/websearch.py`, which was
    the tree's one `infra -> llm` edge: storage reaching sideways into the transport to ask
    a question about a string. Takes the URL rather than a `Config` so it stays answerable
    by anything holding one, which is the property that made it worth owning here.
    """
    return _OPENROUTER_HOST in (base_url or "")


#: What Kith says it is, when a request goes out.
#:
#: `HTTP-Referer` is not a courtesy header. OpenRouter uses it as the *primary identifier* for
#: an app in its rankings and analytics, so it has to be the one URL that means this app and
#: nothing else. Both places that sent it sent `http://localhost` — which identifies every
#: program ever written on a laptop, i.e. nothing at all. The repository is the honest answer:
#: it is public, it is stable, and it is where someone following the attribution would want to
#: land.
APP_URL = "https://github.com/saifullahsaeed/kith"
APP_NAME = "Kith"

#: Which marketplace categories this app belongs to, from OpenRouter's own fixed vocabulary —
#: fifteen slugs across coding, creative, productivity and entertainment. Kith is a peer with a
#: memory that also writes and runs code, so: `personal-agent` first, `programming-app` second.
#:
#: An unrecognised slug is dropped silently rather than refused, so a typo here would never
#: show up as an error — only as an app that quietly belongs to nothing.
APP_CATEGORIES: tuple[str, ...] = ("personal-agent", "programming-app")

#: OpenRouter's per-request ceiling. Sliced rather than trusted, so growing the tuple above
#: cannot start sending a header the other end will partly ignore.
MAX_CATEGORIES = 2


def attribution(base_url: str) -> dict[str, str]:
    """Who is asking, as headers.

    Two places send a chat request — the stream in `llm.openai_compat` and `infra.websearch`,
    which builds its own payload — and both had grown their own copy of this dict. Which is the
    same story `is_openrouter` above is here for, with the same ending: the copies drifted to
    being identical and wrong together, and nothing owned the question.

    `X-OpenRouter-Title` and `X-OpenRouter-Categories` are OpenRouter's vocabulary and go only
    to OpenRouter, like the `web` plugin and the `provider`/`usage` extensions. `X-Title` is the
    older name for the title and still supported there, so it stays — unconditional and
    carrying the same value, which is why sending both cannot mean two different things.
    """
    headers = {"HTTP-Referer": APP_URL, "X-Title": APP_NAME}
    if is_openrouter(base_url):
        headers["X-OpenRouter-Title"] = APP_NAME
        headers["X-OpenRouter-Categories"] = ",".join(APP_CATEGORIES[:MAX_CATEGORIES])
    return headers


def refuses_reasoning(body: str) -> bool:
    """Is this 400 the provider saying reasoning cannot be turned *off*?

    Some models mandate it — "Reasoning is mandatory for this endpoint and cannot be
    disabled", HTTP 400. Asking for it off is still right, because on every other model it
    saves real tokens; being refused just means sending the same request again without the
    switch, rather than failing whatever needed it.

    Matched on the provider's words rather than retried blindly, so a genuine bad request
    still surfaces as one instead of being quietly sent twice. Here, and not in the
    transport where it was born, for exactly the reason `is_openrouter` is here: two
    callers need the answer now — the chat stream and `infra.websearch`, which builds its
    own chat payload — and `infra` reaching sideways into `llm` to ask a question about a
    string is the peer edge the layering forbids.
    """
    return "reasoning" in (body or "").lower()


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
    #: What a cached prompt token costs to read back, and to put there in the first
    #: place. Kith caches every request, so for him these are not a footnote: a warm
    #: round bills most of its prompt at the read rate, typically a tenth of the input
    #: price, while the write is 1.25x. A model chosen on input price alone can lose to
    #: a dearer one that caches better. ``None`` where the provider does not say.
    cache_read_per_mtok: float | None = None
    cache_write_per_mtok: float | None = None
    context: int | None = None
    supports_tools: bool | None = None
    #: Artificial Analysis' agentic index, where the provider publishes one: how well
    #: the model sustains multi-step tool use. The single most relevant number here,
    #: because that is the whole of what Kith does — and the one thing price does not
    #: predict. ``None`` where it has not been measured, which is most of a catalogue.
    agentic_index: float | None = None
    #: How good it is at writing code, and how good it is at everything. Both published
    #: alongside the agentic index and both worth having on screen: agentic ability is
    #: what Kith needs, but "which model should I try today" is a coding question, and
    #: the two orders are not the same — the model at the top of one is regularly third
    #: or fourth on the other.
    coding_index: float | None = None
    intelligence_index: float | None = None
    #: Design Arena's best placing for this model — head-to-head battles judged by
    #: people, rather than an index. Kept as the single best rank it holds across every
    #: arena and category, with the category named, because "1st at data visualisation"
    #: says something an aggregate score cannot.
    arena_rank: int | None = None
    arena_category: str = ""
    arena_elo: float | None = None
    arena_win_rate: float | None = None
    #: The provider's own sentence about it. Two lines of prose beats a row of numbers
    #: for the question "what *is* this model", which is the one you have before you
    #: have any of the others.
    description: str = ""
    #: When its training data ends, and when it was released. The first decides whether
    #: it has heard of the library you are using; the second is most of what "what
    #: should I try today" means.
    knowledge_cutoff: str = ""
    released_on: str | None = None
    #: The most it can write in one reply, which is not the context window and is the
    #: number that stops a long file halfway through.
    max_output: int | None = None
    #: Dollars per web search, where the provider bills them separately.
    web_search_per_call: float | None = None
    #: The weights are published, so the model can outlive the company serving it.
    open_weights: bool = False
    #: ISO date the provider intends to withdraw it. Never recommend one of these.
    retires_on: str | None = None
    #: What it can be *given*: text, image, file, audio, video. Straight from the
    #: provider rather than guessed from the name, because the interface uses it to decide
    #: whether to offer an attach button — and offering one that produces a 400 is worse
    #: than not offering it. Empty means the provider did not say, treated as text-only.
    input_modalities: tuple[str, ...] = ()
    #: Accepts a `reasoning` parameter, so an effort control is meaningful for it.
    supports_reasoning: bool = False

    @property
    def label(self) -> str:
        return self.name or self.id

    @property
    def is_free(self) -> bool:
        """Genuinely free, as opposed to unpriced."""
        return self.prompt_per_mtok == 0 and self.completion_per_mtok == 0

    @property
    def is_recommendable(self) -> bool:
        """Fit to put in front of someone as a suggestion.

        Excludes the variants that work but make a bad thing to be handed as a default:
        ``:free`` is rate-limited hard enough to stall him mid-task, ``:batch`` answers
        in minutes to hours, a model with a withdrawal date stops working on a date
        nobody chose, and a preview can change behaviour or disappear underneath a setup
        that was working. None of them are hidden — all stay searchable in the full list,
        because someone who wants one and knows why should be able to have it.
        """
        return (
            self.supports_tools is not False
            and not self.retires_on
            and ":free" not in self.id
            and not self.id.endswith(":batch")
            and "preview" not in self.id.lower()
        )

    def public(self) -> dict:
        return {
            "id": self.id,
            "name": self.label,
            "promptPerMTok": self.prompt_per_mtok,
            "completionPerMTok": self.completion_per_mtok,
            "cacheReadPerMTok": self.cache_read_per_mtok,
            "cacheWritePerMTok": self.cache_write_per_mtok,
            "context": self.context,
            "supportsTools": self.supports_tools,
            "agenticIndex": self.agentic_index,
            "codingIndex": self.coding_index,
            "intelligenceIndex": self.intelligence_index,
            "arena": (
                {
                    "rank": self.arena_rank,
                    "category": self.arena_category,
                    "elo": self.arena_elo,
                    "winRate": self.arena_win_rate,
                }
                if self.arena_rank
                else None
            ),
            "description": self.description,
            "knowledgeCutoff": self.knowledge_cutoff,
            "releasedOn": self.released_on,
            "maxOutput": self.max_output,
            "webSearchPerCall": self.web_search_per_call,
            "openWeights": self.open_weights,
            "retiresOn": self.retires_on,
            "inputModalities": list(self.input_modalities),
            "supportsImages": "image" in self.input_modalities,
            "supportsFiles": "file" in self.input_modalities,
            "supportsReasoning": self.supports_reasoning,
        }


class Tier(StrEnum):
    """Why a model is being recommended — the three reasons anyone actually picks one.

    Not a ranking of one axis. Someone choosing between these is choosing between
    *kinds* of good: the best available, the best per dollar, or one whose weights are
    published so it cannot be taken away.
    """

    FRONTIER = "frontier"
    VALUE = "value"
    OPEN = "open"


@dataclass(frozen=True)
class Pick:
    """A recommended model, and the evidence for recommending it.

    The reason is not decoration. A bare list of three slugs asks someone to trust it;
    "highest agentic score in the catalogue (55.3)" lets them check it, and disagree.
    """

    tier: Tier
    model_id: str
    headline: str
    reason: str

    def public(self) -> dict:
        return {
            "tier": str(self.tier),
            "modelId": self.model_id,
            "headline": self.headline,
            "reason": self.reason,
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
            kind = ProviderKind.OPENROUTER if is_openrouter(base_url) else ProviderKind.OPENAI_COMPATIBLE
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
