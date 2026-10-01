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

**A quote is still only a prediction, and the bills were already on disk.** Everything above
reads rates the hosts *publish*. Every round Kith sends records what it actually cost, and over
Sep 13-27 on `deepseek/deepseek-v4.1-flash` the two disagreed about the one thing that
mattered. Stored orders put Together first and DeepSeek ninth to thirteenth; Together then
billed $0.044 per million prompt tokens across 2,531 rounds and DeepSeek $0.009 across 5,724.
The gap is behaviour no price list states — 87.6% of Together's prompt tokens came back from
cache against DeepSeek's 96.5%, and 8.0% of its mid-turn rounds lost the cache against 1.4% —
and it cost $87.65 on the other hosts for work DeepSeek's rate would have done for about $21.
So a host with enough rounds on record ranks on its own bill, and the quote survives only for
hosts that have none, calibrated onto the bills' scale. See `priced`.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from dataclasses import dataclass, replace

#: What share of a prompt arrives as cache reads, *for a model this install has never been
#: billed for*. Once a model has rounds on record, its own share is used instead — see
#: `priced` — because this number is a property of the traffic, and the traffic has moved:
#: it was measured as 49,093,108 of 62,270,230 prompt tokens across eight conversations in
#: August, and on deepseek-v4.1-flash over Sep 13-27 it was 0.898.
CACHE_HIT_SHARE = 0.79

#: How much of a round's tokens are the reply, under the same terms: the fallback for a model
#: with no rounds on record. Measured on deepseek-v4.1-flash over Sep 13-27 it was 0.0021 —
#: prompts had grown to hundreds of thousands of tokens against replies in the low thousands —
#: which is why a guess held fixed for every model was weighting completion rates about thirty
#: times too heavily by the time anyone measured it.
OUTPUT_SHARE = 0.06

#: How many rounds a host needs on record before its bill outranks its quote.
#:
#: Measured on deepseek-v4.1-flash over Sep 13-27, by drawing runs of consecutive rounds from
#: each host's own record — which is how evidence about a host arrives, a conversation at a
#: time — and comparing what the run cost with the host's two-week figure. The median error is
#: 1.95x at 10 rounds, 1.54x at 30, 1.48x at 50 and 1.41x at 100. The calibrated quote, given
#: every host's rates fitted from its own bills rather than read off a price list, is off by a
#: median 1.42x; so by 100 rounds a host's bill is at least as good as the best a quote could
#: ever be, and a real price list is worse than that fit.
#:
#: Erring high is the cheaper mistake. A host judged on too few rounds and demoted stops being
#: sent conversations and so never collects the rounds that would clear it; a host judged on
#: too many is still leading, and is collecting them.
MIN_ROUNDS = 100

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
    #: The weights this host actually serves — "fp8", "bf16", "int4", "" when unstated.
    #: Four-bit weights rank behind everything else that is healthy; see `LOSSY`.
    quantization: str = ""
    #: What this host will actually accept, which is not always what the model advertises: a
    #: 256k model is routinely served by hosts capped at 32k, and the round that overflows is
    #: the first anyone hears of it.
    context_length: int = 0
    #: And what it will write back. 0 means the host did not say.
    max_completion_tokens: int = 0
    #: OpenRouter's own health flag. Below zero means deranked or disabled.
    status: int = 0
    uptime: float = 100.0
    #: The figure the ranking compares, once `priced` has put every host on one scale: the
    #: host's own bill when it has `MIN_ROUNDS` on record, its calibrated quote when it does
    #: not. None on a row that was never priced, which ranks on `effective_price` alone.
    cost_per_mtok: float | None = None
    #: How many recorded rounds `cost_per_mtok` rests on. 0 means it is an estimate.
    billed_rounds: int = 0

    def public(self) -> dict:
        """This host as the picker shows it — every number it would need to justify a pin.

        The price is computed here rather than left to the client because it is the number
        the ranking is actually made on, and a picker that ordered rows by one figure while
        displaying another would be the same silent mismatch this module exists to fix.
        `billedRounds` says which kind of number it is: a bill, or an estimate at 0.
        """
        return {
            "slug": self.slug,
            "name": self.name or self.slug,
            "quantization": self.quantization,
            "contextLength": self.context_length,
            "maxCompletionTokens": self.max_completion_tokens,
            "promptPerMtok": _finite(self.prompt_per_mtok),
            "completionPerMtok": _finite(self.completion_per_mtok),
            "cacheReadPerMtok": _finite(self.cache_read_per_mtok),
            "effectivePerMtok": _finite(_cost(self)),
            "billedRounds": self.billed_rounds,
            "throughput": self.throughput,
            "latencyMs": _finite(self.latency_ms),
            "uptime": self.uptime,
            "healthy": _healthy(self),
        }


#: A host this far below perfect over the last half hour is not worth being first in line for,
#: whatever it charges. It stays in the list — `allow_fallbacks` would reach it anyway — it just
#: does not lead.
MIN_UPTIME = 95.0


#: Quantizations that do not hold a long conversation together, so they never lead. Measured on
#: `deepseek/deepseek-v4.1-flash` at 500k-800k prompt tokens: 33 of 43 rounds served by
#: `open-inference/fp4` reasoned as if the conversation had just begun — "the user asked a vague
#: question", re-reading the first message as the current one — against 6 of 306 on Morph, from
#: the same transcript. Cheap enough to rank third on price, which is how it got the rounds.
#: Like an unhealthy host it stays in the list, below the rest: a preference, not a ban.
LOSSY = frozenset({"fp4", "int4"})


@dataclass(frozen=True)
class Bill:
    """What one host has actually charged for one model, summed over every round it served.

    Named by `provider` as a round reports it — "DeepSeek", "DeepInfra" — because that is all
    a response says about where it ran. Matching it to a slug is `_bill_for`'s job, done
    against the endpoint table, which is the one place that has both spellings.
    """

    provider: str
    rounds: int = 0
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0

    def per_mtok(self) -> float:
        """Dollars per million tokens of traffic — the unit `effective_price` quotes in, so a
        bill and a quote are the same kind of number and the only difference left between them
        is the one being measured."""
        tokens = self.prompt_tokens + self.completion_tokens
        return self.cost_usd / tokens * 1_000_000 if tokens > 0 else float("inf")


def effective_price(
    endpoint: Endpoint, cache_share: float = CACHE_HIT_SHARE, output_share: float = OUTPUT_SHARE
) -> float:
    """What a round on this host would cost going by its quote, in dollars per million tokens.

    It is worth being explicit rather than reaching for the headline rate the way OpenRouter's
    own sort does. A prompt token costs the fresh rate when it misses the cache and the read
    rate when it hits, so the input rate is the two blended at the ratio they actually arrive
    in. That one line is the entire difference from `sort`: at the fallback share it separates
    three hosts quoting an identical $0.150 into $0.0339, $0.0434 and $0.0434, because one of
    them reads cache at a fifth of what the others charge.

    Output is folded in at the share of a round it really is rather than left out, so a host
    cannot quote a cheap completion rate on a dear prompt and buy its way up.

    The two shares are the traffic's, not the host's: `priced` passes the ones this model's
    recorded rounds actually show, and the constants stand in only for a model never billed.
    For a host with enough rounds of its own none of this is consulted — its bill is the price.

    An unquoted rate arrives as `inf` from `_per_million` and stays `inf` through this: a host
    that does not publish a price must never rank as though it were free.
    """
    inputs = (1 - cache_share) * endpoint.prompt_per_mtok + cache_share * endpoint.cache_read_per_mtok
    return (1 - output_share) * inputs + output_share * endpoint.completion_per_mtok


def priced(endpoints: list[Endpoint], bills: Iterable[Bill]) -> list[Endpoint]:
    """The same rows, each carrying what it should be expected to cost, all on one scale.

    A host with `MIN_ROUNDS` on record is priced at its bill. That already contains everything
    a price list leaves out — how much of the prompt it really serves from cache, how often it
    loses the cache mid-turn, what a cache read really costs there — which is why it outranks
    any arithmetic on quotes.

    A host without that many is priced at its quote, with two corrections that both come from
    the bills. The quote is for traffic shaped the way this model's actually is, not the way
    `CACHE_HIT_SHARE` guessed. And it is scaled by how far the billed hosts' bills sat from
    their own quotes: the median of those ratios, because the question is what an ordinary host
    does, and one host with a pathological cache should not answer it. With each host's rates
    fitted from its Sep 13-27 bills, that factor came out at 1.33 — a typical host billed a third
    above what its rates predict for the same traffic — so an unbilled host is assumed ordinary
    rather than assumed to be the one host whose cache never breaks. That is what lets it be
    compared with a bill at all.

    With no billed host there is nothing to calibrate against and the factor is 1; with no
    rounds of this model at all the shares are the fallbacks — which together are exactly the
    ranking this module made before it read a bill.
    """
    bills = [one for one in bills if one.rounds > 0 and _host(one.provider)]
    by_host = {_host(one.provider): one for one in bills}
    cache_share, output_share = _shape(bills)
    quotes = [effective_price(one, cache_share, output_share) for one in endpoints]
    matched = [_bill_for(one, by_host) for one in endpoints]
    evidenced = [bill if bill is not None and bill.rounds >= MIN_ROUNDS else None for bill in matched]
    ratios = [
        bill.per_mtok() / quote
        for bill, quote in zip(evidenced, quotes, strict=True)
        if bill is not None and 0 < quote < float("inf")
    ]
    scale = statistics.median(ratios) if ratios else 1.0
    return [
        replace(one, cost_per_mtok=bill.per_mtok(), billed_rounds=bill.rounds)
        if bill is not None
        else replace(one, cost_per_mtok=quote * scale)
        for one, bill, quote in zip(endpoints, evidenced, quotes, strict=True)
    ]


def _shape(bills: list[Bill]) -> tuple[float, float]:
    """This model's cache share and output share, pooled over every host that served it, or
    the fallbacks when nothing has. Pooled because they describe what Kith sends, which does
    not change with where it lands."""
    prompt = sum(one.prompt_tokens for one in bills)
    completion = sum(one.completion_tokens for one in bills)
    if prompt <= 0:
        return CACHE_HIT_SHARE, OUTPUT_SHARE
    return sum(one.cached_tokens for one in bills) / prompt, completion / (prompt + completion)


def _host(text: str) -> str:
    """A provider's name reduced to what its display name and its slug agree on.

    A round reports "DeepInfra" or "OpenInference"; the table's slugs are `deepinfra/fp8` and
    `open-inference/fp4`. Case, punctuation and the quantization suffix are the whole of the
    difference, so they are what is dropped.
    """
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _bill_for(endpoint: Endpoint, by_host: dict[str, Bill]) -> Bill | None:
    """This row's bill, matched on the table's display name first — the same field a round's
    `provider` is read from — and on the slug's host part when the row has no name.

    Two rows from one host — `novita/fp8` and `novita/bf16`, or `baseten/fp8` listed twice —
    share one bill, because a round does not say which of them served it.
    """
    for key in (_host(endpoint.name), _host(endpoint.slug.split("/")[0])):
        if key and key in by_host:
            return by_host[key]
    return None


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

    Unhealthy hosts sink to the bottom rather than being dropped, and four-bit hosts sink just
    above them: taking a host off the list entirely is a decision about availability, and this
    function is only making one about preference. OpenRouter is free to reach them once everything above has failed.

    Every host is listed, not a top few. A short list means the fallback past the end of it is
    unranked again, which is the behaviour this replaces.
    """
    keys = _KEYS.get(by) or _KEYS["price"]
    healthy = [one for one in endpoints if _healthy(one) and one.quantization not in LOSSY]
    lossy = [one for one in endpoints if _healthy(one) and one.quantization in LOSSY]
    rest = [one for one in endpoints if not _healthy(one)]
    return _deduped(one.slug for one in _banded(healthy, keys) + _banded(lossy, keys) + _banded(rest, keys))


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
    return endpoint.cost_per_mtok if endpoint.cost_per_mtok is not None else effective_price(endpoint)


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


def serves(hosts: list[Endpoint], pinned: str) -> bool:
    """Would OpenRouter's `provider.order` find ``pinned`` among these hosts?

    It takes three spellings, and all three are in use here: a host's tag (`openai/flex`), the
    provider part of one (`openai`, which matches all of that provider's endpoints), and the
    display name a person types from the website (`DeepSeek`).
    """
    want = pinned.strip().lower()
    if not want:
        return False
    for one in hosts:
        tag = one.slug.lower()
        name = one.name.lower()
        if want in (tag, tag.split("/")[0], name, name.replace(" ", "")):
            return True
    return False


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
        quantization=str(row.get("quantization") or ""),
        context_length=int(_number(row.get("context_length"))),
        max_completion_tokens=int(_number(row.get("max_completion_tokens"))),
        status=int(row.get("status") or 0),
        uptime=_number(row.get("uptime_last_30m"), default=100.0),
    )


def _per_million(raw: object) -> float:
    """Dollars per token as a string, to dollars per million. Unknown reads as very expensive."""
    try:
        return float(raw) * 1_000_000  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("inf")


def _finite(value: float) -> float | None:
    """A number the wire can carry. `inf` is how this module says "not quoted", and JSON has
    no word for it — `Infinity` is not valid JSON and every parser disagrees about it — so it
    crosses as null and the picker shows a dash rather than a host that looks free."""
    return value if value not in (float("inf"), float("-inf")) else None


def _number(raw: object, default: float = 0.0) -> float:
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
