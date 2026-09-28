"""What each host has actually charged, kept where the host ranking can read it.

Every round Kith sends comes back with its cost and the host that served it, and every one of
those has been written to a transcript since the field existed. The ranking never looked: it
predicted each host's bill from its published rates, and the prediction led with a host that
billed nearly five times what the cheapest one did. This is the record that lets
`domain.endpoints.priced` read the bills instead — per (model, provider), a running total of
rounds, tokens and dollars.

**Two ways in, and they cannot count a round twice.** `observe` adds each round as it happens.
`fill` builds the whole record from the transcripts once, the first time a ranking needs it and
finds nothing there — which is also how the history that predates this module gets in. A round
observed before that fill is skipped rather than counted, because it is already in its
transcript and the fill will count it from there.

The record is the install's, like the transcripts it is built from, so it lives in the install's
database, read through `settings` at call time so a redirected database is the one used.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterable
from pathlib import Path

from kith import settings
from kith.domain.endpoints import Bill
from kith.infra.db import repositories as repo

log = logging.getLogger(__name__)

#: Serialises this process's fills, so two conversations ranking at the same moment do not both
#: read 180 MB of transcripts to write one record. Another process is kept out by the primary key
#: instead; see `repositories.host_costs.fill`.
_filling = threading.Lock()


def observe(stats: dict) -> None:
    """Count one round's bill against the host that served it.

    Takes the `stats` dict exactly as the round's event carries it: the model and the host are
    fields of it, so there is nothing to pass alongside. A round with no host named — a local
    model, a plain OpenAI-compatible endpoint — is not a host's bill and is not counted.

    Never raises. This runs once per round inside a live turn, and a bookkeeping failure must
    cost the bookkeeping, not the turn.
    """
    try:
        model, provider = str(stats.get("model") or ""), str(stats.get("provider") or "")
        if not model or not provider:
            return
        repo.host_costs.add(
            settings.AGENT_DB_PATH,
            model,
            provider,
            prompt=int(stats.get("promptTokens") or 0),
            cached=int(stats.get("cachedTokens") or 0),
            completion=int(stats.get("responseTokens") or 0),
            cost=float(stats.get("costUsd") or 0.0),
        )
    except Exception as error:
        log.debug("could not record a round: %s", error)


def bills(model: str) -> list[Bill]:
    """Every host's bill for `model`, filling the record from the transcripts first if it has
    never been filled.

    Total, like the ranking that calls it: not knowing costs the ranking its bills and leaves it
    on quotes, which is the behaviour before this module existed, and is no reason to fail a turn.
    """
    try:
        if not repo.host_costs.started(settings.AGENT_DB_PATH):
            fill()
        rows = repo.host_costs.for_model(settings.AGENT_DB_PATH, model)
    except Exception as error:
        log.debug("could not read host bills for %s: %s", model, error)
        return []
    return [
        Bill(
            provider=str(row["provider"]),
            rounds=int(row["rounds"]),
            prompt_tokens=int(row["prompt_tokens"]),
            cached_tokens=int(row["cached_tokens"]),
            completion_tokens=int(row["completion_tokens"]),
            cost_usd=float(row["cost_usd"]),
        )
        for row in rows
    ]


def fill(directory: Path | None = None) -> int:
    """Build the record from every transcript, if it is empty. Returns the rounds it counted.

    Safe to run by hand and safe to run twice: a record that already has rows is left exactly as
    it is, because `observe` has been adding to it since and re-reading the transcripts over the
    top would count every one of those rounds again.

    One pass, once. Only lines that mention `"stats"` are parsed at all — on this install that is
    37,697 lines out of 181 MB, and the whole pass took 0.23 s — so the cost is paid by the first
    ranking after an upgrade. An install whose transcripts name no host yet has nothing to write
    and reads them again at its next ranking, until its first billed round lands in one.
    """
    with _filling:
        if repo.host_costs.started(settings.AGENT_DB_PATH):
            return 0
        totals = _totals(directory or _transcripts())
        rows = [
            {"model": model, "provider": provider, **counts} for (model, provider), counts in totals.items()
        ]
        if not repo.host_costs.fill(settings.AGENT_DB_PATH, rows):
            return 0
        return sum(row["rounds"] for row in rows)


def _transcripts() -> Path:
    from kith.services import conversations

    return conversations.directory()


def _totals(directory: Path) -> dict[tuple[str, str], dict]:
    totals: dict[tuple[str, str], dict] = {}
    for path in sorted(directory.glob("*.jsonl")):
        for stats in _stats_in(path):
            model, provider = str(stats.get("model") or ""), str(stats.get("provider") or "")
            if not model or not provider:
                continue
            counts = totals.setdefault(
                (model, provider),
                {
                    "rounds": 0,
                    "prompt_tokens": 0,
                    "cached_tokens": 0,
                    "completion_tokens": 0,
                    "cost_usd": 0.0,
                },
            )
            counts["rounds"] += 1
            counts["prompt_tokens"] += int(stats.get("promptTokens") or 0)
            counts["cached_tokens"] += int(stats.get("cachedTokens") or 0)
            counts["completion_tokens"] += int(stats.get("responseTokens") or 0)
            counts["cost_usd"] += float(stats.get("costUsd") or 0.0)
    return totals


def _stats_in(path: Path) -> Iterable[dict]:
    """The `stats` events of one transcript, skipping anything that does not parse.

    A transcript is append-only and a crash mid-write costs its last line, so a torn line is an
    expected thing to meet here and not a reason to give up on the file, let alone the fill.
    """
    try:
        with path.open("rb") as handle:
            for line in handle:
                if b'"stats"' not in line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if (
                    isinstance(event, dict)
                    and event.get("type") == "stats"
                    and isinstance(event.get("stats"), dict)
                ):
                    yield event["stats"]
    except OSError as error:
        log.debug("could not read %s: %s", path, error)
