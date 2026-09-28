"""What each host has charged for each model, summed — the bills host ranking reads.

One row per (model, provider), holding running totals rather than one row per round, because
the only question ever asked of it is "what does this host cost per token, all told" and the
per-round detail already lives in the transcripts, which is where `fill` rebuilds it from.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError

from kith.infra.db.engine import as_dict, session
from kith.infra.db.models import HostCost


def add(
    path: Path, model: str, provider: str, prompt: int, cached: int, completion: int, cost: float
) -> bool:
    """Count one round, if the record has been started. Returns whether it was counted.

    An upsert that adds in the database rather than a read followed by a write, because two
    turns can finish a round at the same moment and a read-modify-write would lose one of them.

    A round arriving before the record was ever filled is left out on purpose: it is already in
    its transcript, `fill` will read it from there, and counting it here as well would count it
    twice. Checked inside the same transaction as the write so the answer cannot go stale
    between them.
    """
    with session(path) as db:
        if db.scalar(select(HostCost.model).limit(1)) is None:
            return False
        statement = insert(HostCost).values(
            model=model,
            provider=provider,
            rounds=1,
            prompt_tokens=prompt,
            cached_tokens=cached,
            completion_tokens=completion,
            cost_usd=cost,
        )
        db.execute(
            statement.on_conflict_do_update(
                index_elements=[HostCost.model, HostCost.provider],
                set_={
                    "rounds": HostCost.rounds + 1,
                    "prompt_tokens": HostCost.prompt_tokens + statement.excluded.prompt_tokens,
                    "cached_tokens": HostCost.cached_tokens + statement.excluded.cached_tokens,
                    "completion_tokens": HostCost.completion_tokens + statement.excluded.completion_tokens,
                    "cost_usd": HostCost.cost_usd + statement.excluded.cost_usd,
                },
            )
        )
        return True


def for_model(path: Path, model: str) -> list[dict]:
    with session(path) as db:
        return [as_dict(row) for row in db.scalars(select(HostCost).where(HostCost.model == model)).all()]


def started(path: Path) -> bool:
    """Whether the record holds anything at all — the condition `fill` runs on."""
    with session(path) as db:
        return db.scalar(select(HostCost.model).limit(1)) is not None


def fill(path: Path, rows: list[dict]) -> bool:
    """Write a whole record at once, if there is none yet. Returns whether this call wrote it.

    All in one transaction, so the record is either empty or complete and a reader never sees
    half a history as if it were all of it. Two callers racing to fill — two processes, since
    the service serialises its own threads — cannot both succeed: the second collides on the
    primary key and rolls back whole, rather than doubling every total.
    """
    if not rows:
        return False
    try:
        with session(path) as db:
            if db.scalar(select(HostCost.model).limit(1)) is not None:
                return False
            db.add_all([HostCost(**row) for row in rows])
            return True
    except IntegrityError:
        return False
