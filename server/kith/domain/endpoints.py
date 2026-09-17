"""Which *host* serves a round, decided here rather than delegated to a one-word sort.

`prefer_provider_by: "price"` puts `provider: {sort: "price"}` on the wire and it does work —
measured over 428 recorded rounds of `deepseek/deepseek-v4.1-flash`, every host that served one
was among the six cheapest of the model's nineteen, and nothing at $0.285 or above was ever
touched. So the setting is honoured. It is also not enough, for two reasons that only show up
once you look at what the six actually cost and how fast they are:

    host        list in/out    cache read   measured $/round   measured tok/s   rounds
    Alibaba     0.150 0.600    0.0150       0.0059             73.9             164
    Morph       0.210 0.840    0.0063       0.0060             11.4              86
    Relace      0.150 0.600    0.0150       0.0087             38.3              79
    Fireworks   0.220 0.660    0.0070       0.0058             68.0              67
    DeepInfra   0.200 0.600    0.0060       0.0159             30.1              24
    DeepSeek    0.150 0.600    0.0030       0.0054            146.4               8

**`sort` ranks on the wrong price.** It reads the headline prompt and completion rates. 78.8% of
this install's prompt tokens are cache *reads* — that is what `llm/caching.py` exists to
achieve — so the rate that decides the bill is `input_cache_read`, and on that the three hosts
`sort` calls a tie at $0.150 are not a tie at all: DeepSeek reads cache at $0.003 against
Alibaba's and Relace's $0.015, five times less. `domain.connection.ModelInfo` already says this
in prose — "a model chosen on input price alone can lose to a dearer one that caches better" —
and it was true of hosts the whole time, where nothing acted on it.

**`sort` has no tie-break.** Three hosts quote $0.150 and OpenRouter picks among them by
something that is not speed. DeepSeek answers at 146 tok/s and got 8 rounds; Morph answers at
11 and got 86. That is the same model, thirteen times slower, for slightly more money.

So the decision needs two keys — effective price first, throughput to break ties — and OpenRouter
has no sort that expresses it. What it does have is `provider.order`, an explicit list. This
module turns the model's endpoint table into that list. It is pure: rows in, slugs out. The
fetching and caching is `services/routing.py`, and the payload field is
`llm/openai_compat._routing_options`.

`throughput` and `latency` get the same treatment with the keys swapped, because the gap is
symmetric: OpenRouter's throughput sort has no price tie-break, and on the same table Reka
(110 tok/s, $0.29) and GMICloud (89 tok/s, $0.285) sit beside DeepSeek (87 tok/s, $0.15) at
roughly double the money. Asking for speed is not the same as saying price stopped counting.

Nothing here reduces availability. The order ships with `allow_fallbacks` on, exactly as a pin
does, so a host being down costs a step down the list rather than a failed turn — and the step
now lands on the next best host instead of an arbitrary one.
"""

from __future__ import annotations

from dataclasses import dataclass

#: What share of a prompt arrives as cache reads, used to weigh the read rate against the
#: fresh one. Measured, not guessed: 49,093,108 of 62,270,230 recorded prompt tokens on this
#: install, across the last eight conversations. A constant rather than a setting because it
#: is a property of how Kith prompts — one long stable prefix per conversation — and not a
#: preference anyone should have to hold an opinion about.
CACHE_HIT_SHARE = 0.79

#: How much of a round's tokens are the reply. Prompts here run to tens of thousands of tokens
#: against replies in the hundreds, so output price is a real but small share of the bill, and a
#: host cannot buy its way up this ranking by quoting a cheap completion rate on a dear prompt.
OUTPUT_SHARE = 0.06

#: Values within this of each other are the same value, and the other key decides between them.
#: The three hosts at $0.150 are the case it was written for. Too tight and an accounting
#: rounding puts a 13 tok/s host ahead of a 146 tok/s one; too loose and "cheapest" — or
#: "fastest" — quietly stops meaning it.
TIE_WINDOW = 0.05


@dataclass(frozen=True)
class Endpoint:
    """One host serving one model, in the shape the ranking needs.

    Straight off `GET /api/v1/models/{model}/endpoints`, with the two fields that matter most
    — the cache-read rate and measured throughput — kept as first-class rather than buried in
    the nested dicts they arrive in. Prices are dollars per million tokens.
    """

    #: The provider slug, which is what `provider.order` takes: "deepseek", "morph/fp8".
    slug: str
    #: The display name, for saying out loud why an order looks the way it does.
    name: str = ""
    prompt_per_mtok: float = 0.0
    completion_per_mtok: float = 0.0
    #: What a cached prompt token costs to read back. 0.0 is a real answer here — several
    #: hosts cache for free — so it is not a stand-in for "unknown".
    cache_read_per_mtok: float = 0.0
    #: Median tokens per second over the last half hour, as OpenRouter measures it.
    throughput: float = 0.0
    #: Median milliseconds to the first token. The second speed number, and the one a person
    #: feels on a short round where throughput never gets going. Unknown is infinite rather
    #: than zero: zero reads as instant, which would put an unmeasured host at the front of
    #: every band it lands in.
    latency_ms: float = float("inf")
    #: OpenRouter's own health flag. Below zero means deranked or disabled.
    status: int = 0
    uptime: float = 100.0


#: A host this far below perfect over the last half hour is not worth being first in line for,
#: whatever it charges. It stays in the list — `allow_fallbacks` would reach it anyway — it just
#: does not lead.
MIN_UPTIME = 95.0


def effective_price(endpoint: Endpoint) -> float:
    """What a round on this host actually costs, in dollars per million tokens.

    This is the whole of what "cheapest" means here, and every ordering decision falls out of
    it, so it is worth being explicit rather than reaching for the headline rate the way
    OpenRouter's own sort does.

    A prompt token costs the fresh rate when it misses the cache and the read rate when it
    hits, so the input rate is the two blended at the ratio they actually arrive in. That one
    line is the entire difference from `sort`: on the measured table it separates three hosts
    quoting an identical $0.150 into $0.0339, $0.0434 and $0.0434, because one of them reads
    cache at a fifth of what the others charge.

    Output is folded in at the share of a round it really is rather than left out, so a host
    cannot quote a cheap completion rate on a dear prompt and buy its way up — and cannot be
    ignored either, which at 4x the prompt rate it should not be.

    An unquoted rate arrives as `inf` from `_per_million` and stays `inf` through this: a host
    that does not publish a price must never rank as though it were free.
    """
    inputs = (1 - CACHE_HIT_SHARE) * endpoint.prompt_per_mtok + CACHE_HIT_SHARE * endpoint.cache_read_per_mtok
    return (1 - OUTPUT_SHARE) * inputs + OUTPUT_SHARE * endpoint.completion_per_mtok


def _healthy(endpoint: Endpoint) -> bool:
    return endpoint.status >= 0 and endpoint.uptime >= MIN_UPTIME


def rank(endpoints: list[Endpoint], by: str = "price") -> tuple[str, ...]:
    """The hosts for this model, best first by `by`, with the other key breaking ties.

    Symmetric on purpose. Ranking by price and then ignoring speed among equals is the bug this
    module was written for; ranking by speed and then ignoring price among equals is the same
    bug facing the other way, and on the measured table it is not hypothetical — Reka at
    110 tok/s and GMICloud at 89 sit beside DeepSeek at 87, costing roughly double. Someone who
    asks for throughput has said speed matters more than money, not that money stopped
    mattering.

    Unhealthy hosts sink to the bottom rather than being dropped: taking a host off the list
    entirely is a decision about availability, and this function is only making one about
    preference. OpenRouter is free to reach them once everything above has failed.

    Every host is listed, not a top few. A short list means the fallback past the end of it is
    unranked again, which is the behaviour this replaces.
    """
    keys = _KEYS.get(by) or _KEYS["price"]
    healthy = [one for one in endpoints if _healthy(one)]
    rest = [one for one in endpoints if not _healthy(one)]
    return _deduped(one.slug for one in _banded(healthy, keys) + _banded(rest, keys))


def _deduped(slugs) -> tuple[str, ...]:
    """The slugs in order, each appearing once.

    A slug is not a key. `deepseek/deepseek-v4.1-flash` really does list two endpoints tagged
    `baseten/fp8` — same host, same quantization, different deployments, and the table gives no
    way to tell them apart. Sent as-is that is a `provider.order` with a repeat in it, which is
    at best ignored and at worst rejected, and either way it is a list that says something we
    did not mean. The first occurrence is the better-ranked one, so keeping it and dropping the
    rest is also the right choice and not merely the easy one.

    Blank slugs go with them: a row we could not read a tag from cannot be ordered, and an
    empty string in the list would be a filter matching nothing.
    """
    seen: set[str] = set()
    out: list[str] = []
    for slug in slugs:
        if slug and slug not in seen:
            seen.add(slug)
            out.append(slug)
    return tuple(out)


def _cost(endpoint: Endpoint) -> float:
    return effective_price(endpoint)


def _slowness(endpoint: Endpoint) -> float:
    """Throughput as a cost, so every key in here is a thing you want less of.

    Ordering by "more is better" and "less is better" in the same function is where an off-by-a
    -sign lives, and it is invisible: a reversed key still produces a plausible-looking list.
    Zero tok/s — which is what an unmeasured host reports — becomes infinitely slow, which is
    the right place for a host nobody has numbers on.
    """
    return 1 / endpoint.throughput if endpoint.throughput > 0 else float("inf")


def _wait(endpoint: Endpoint) -> float:
    return endpoint.latency_ms


#: What each setting ranks on, and what settles a tie — the third key settling a tie in the
#: second. Every key is "lower is better", so the banding below never has to know which way
#: round an ordering goes; `_slowness` exists only to put throughput in those terms.
_KEYS: dict[str, tuple] = {
    "price": (_cost, lambda e: (_slowness(e), _wait(e))),
    "throughput": (_slowness, lambda e: (_cost(e), _wait(e))),
    "latency": (_wait, lambda e: (_cost(e), _slowness(e))),
}


#: The orderings this module can rank. Anything else — blank included — is OpenRouter's own
#: business and is left to it.
ORDERINGS = frozenset(_KEYS)


def _banded(endpoints: list[Endpoint], keys: tuple) -> list[Endpoint]:
    """Primary order, with each band of near-equal values re-sorted by the secondary key.

    Banding is done by walking the sorted list rather than by rounding to a grid, because a grid
    puts the boundary in an arbitrary place: $0.149 and $0.151 are the same price and any fixed
    bucket edge eventually falls between them.
    """
    primary, secondary = keys
    ordered = sorted(endpoints, key=primary)
    out: list[Endpoint] = []
    band: list[Endpoint] = []
    for one in ordered:
        if band and primary(one) > primary(band[0]) * (1 + TIE_WINDOW):
            out.extend(sorted(band, key=secondary))
            band = []
        band.append(one)
    out.extend(sorted(band, key=secondary))
    return out


def from_api(row: dict) -> Endpoint:
    """One row of the endpoints response, read defensively.

    Prices arrive as strings in dollars per token, throughput and latency as percentile dicts.
    A field this cannot read becomes the value that keeps the host in the running but not at the
    front — a missing price must never read as free, which is how an unpriced host would win
    every band it appeared in.
    """
    pricing = row.get("pricing") or {}
    throughput = row.get("throughput_last_30m") or {}
    latency = row.get("latency_last_30m") or {}
    return Endpoint(
        slug=str(row.get("tag") or ""),
        name=str(row.get("provider_name") or ""),
        prompt_per_mtok=_per_million(pricing.get("prompt")),
        completion_per_mtok=_per_million(pricing.get("completion")),
        cache_read_per_mtok=_per_million(pricing.get("input_cache_read")),
        throughput=_number(throughput.get("p50")),
        latency_ms=_number(latency.get("p50"), default=float("inf")),
        status=int(row.get("status") or 0),
        uptime=_number(row.get("uptime_last_30m"), default=100.0),
    )


def _per_million(raw: object) -> float:
    """Dollars per token as a string, to dollars per million. Unknown reads as very expensive."""
    try:
        return float(raw) * 1_000_000  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("inf")


def _number(raw: object, default: float = 0.0) -> float:
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
