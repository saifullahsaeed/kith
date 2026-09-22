"""Which hosts serve a model, so that pinning one is a choice rather than a spelling test.

`openrouter_provider` has been settable since the beginning and effectively unusable. It takes
a provider slug — `deepinfra`, `morph/fp8` — and nothing in Kith has ever printed one, so using
it meant knowing the slug from OpenRouter's own site and typing it correctly first time. A
wrong one does not fail: `provider.order` with an unrecognised name is ignored, fallbacks carry
the request, and the setting sits there reading back exactly as typed, doing nothing.

So this is the list the picker needs, and it is deliberately the *same* list the router ranks
— `services.routing.table` over `domain.endpoints` — rather than a second fetch with its own
opinion. A settings screen that ordered hosts differently from the router would be a new way to
be wrong about the same question.

It answers for the model currently configured unless asked about another, because the pin is
install-wide while hosts are per-model: a slug that serves one model frequently serves none of
the next one. `pinnedServesModel` is how the screen says so out loud instead of leaving a dead
setting looking live.
"""

from __future__ import annotations

from dataclasses import replace

from flask import jsonify, request

from kith.api.blueprint import api
from kith.config import default_config
from kith.domain.connection import is_openrouter
from kith.services import routing, tuning


@api.get("/models/endpoints")
@api.doc(
    summary="The hosts that serve a model, ranked the way this install would route to them",
    description=(
        "`model` defaults to the configured one. Rows carry each host's slug, quantization, "
        "context cap, the three published rates and the blended price the ranking is actually "
        "made on, plus measured throughput, latency and uptime. Empty for a connection that is "
        "not OpenRouter, or when the endpoint table could not be read — neither is an error, "
        "and both mean the same thing to the screen: there is nothing here to pin to."
    ),
)
def model_endpoints():
    config = default_config()
    model = (request.args.get("model") or "").strip() or config.model
    config = replace(config, model=model)
    settings = tuning.routing()

    supported = bool(config.api_key) and is_openrouter(config.base_url)
    rows = routing.table(config, settings.prefer_by) if supported else []
    slugs = {one.slug for one in rows}
    return jsonify(
        {
            "model": model,
            # Why the list is empty, which is the difference between "not an OpenRouter key"
            # and "OpenRouter had nothing to say" — the screen wants to word those differently.
            "supported": supported,
            "pinned": settings.pinned,
            # A pin naming a host that does not serve this model is doing nothing at all. Said
            # here rather than inferred in the UI so both answers come from one place.
            "pinnedServesModel": (not settings.pinned) or settings.pinned in slugs,
            "rankedBy": settings.prefer_by,
            "rows": [one.public() for one in rows],
        }
    )
