"""The resolved parameters of one model request.

A value type, and nothing else: no storage, no environment, no persona. Resolving these —
which is `config.default_config()` — means reading the settings database and merging the
persona with the skill index, so it reaches into two services and belongs at that layer.
Holding the answer does not.

The split is here because `llm/` and `infra/` need the type and must not import `services/`.
Both wrote `from kith.config import Config` at module scope, which pulled in a module whose
own header imports `kith.services.skills` and `kith.services.persona` — three of this
codebase's nineteen import cycles, to name a frozen dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    """A resolved set of chat parameters."""

    model: str
    num_ctx: int
    num_predict: int
    system: str
    think: bool
    #: How hard to think before answering: "" (leave it to the provider), or any value in
    #: `kith.domain.enums.REASONING_EFFORTS`. Only sent to models whose OpenRouter
    #: entry lists `reasoning` in supported_parameters — sending it elsewhere is a 400 on
    #: the whole request, not a field quietly ignored.
    effort: str = ""
    #: OpenRouter stickiness for this conversation, so its rounds land on one warm cache.
    #: Blank falls back to the install-wide id.
    session_id: str = ""
    base_url: str = ""  # OpenAI-compatible cloud endpoint; blank = local Ollama
    api_key: str = ""  # cloud API key; when set (with base_url), chat runs in the cloud
    #: How many tokens this model can hold, or 0 when nobody knows.
    #:
    #: A fact about the model rather than a setting, which is why it is resolved here and is
    #: not in the tuning registry. 0 is load-bearing and must never be replaced by a guess: a
    #: window guessed too high never fires and every turn 400s; guessed too low, it truncates
    #: work that would have fitted. "I don't know" has to stay expressible.
    context_window: int = 0


@dataclass(frozen=True)
class Routing:
    """How a cloud request should be steered, resolved from settings before it is sent.

    A value type for the same reason `Config` is one. `llm/openai_compat.py` builds the
    payload and used to read all six of these out of `services.tuning` itself — which meant
    the transport, whose job is to put bytes on a socket, importing the settings service to
    decide which upstream to prefer. That was the last `llm -> services` edge, and it was two
    function-body imports written to keep Python from noticing.

    The defaults here are the tunables' own declared defaults, so a `Routing()` behaves
    exactly as an unconfigured install does, and the loop — a service, where reading a setting
    is allowed — resolves the live one once per turn and hands it down.
    """

    #: An upstream to pin to. A strong preference, not a lock: fallbacks stay on.
    pinned: str = ""
    #: How to order the pool when nothing is pinned. "price" is what keeps a session landing
    #: somewhere cheap rather than merely somewhere consistent.
    prefer_by: str = "price"
    #: A ceiling per million prompt tokens, for when `prefer_by` cannot help because every
    #: cheap host is busy. 0 means no ceiling — a figure set too low takes the model off air.
    max_prompt_price: float = 0.0
    #: Only route to upstreams supporting everything this request sends, so a cheaper host
    #: cannot silently drop a feature that was paid for.
    require_parameters: bool = True
    #: Exclude any provider that may log or train on the request.
    zero_data_retention: bool = False
    #: A second model to try if the primary errors or is down. Blank means no fallback.
    fallback_model: str = ""
