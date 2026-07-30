"""The models must describe the schema the migrations actually build.

This is the test that matters most for the data layer. The ORM models are hand-
written to mirror `migrations.py` rather than to define it, so the two can drift —
and drift here does not raise on import or on a read. It surfaces as a write that
silently fails, or a column that is always None.

It also pins the two singleton rows. Both were found EMPTY in the live database:
a brain wipe had deleted them without re-seeding, and every `set_mood` /
`set_self` call had been raising TypeError ever since — which meant Kith could not
record his own identity at all, and nothing anywhere said so.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect

from kith.infra.db.engine import get_engine
from kith.infra.db.models import Base

# Tables the migrations create that no model needs to describe.
_NOT_MODELLED = {"sqlite_sequence"}


def test_every_table_has_a_model(db: Path) -> None:
    live = set(inspect(get_engine(db)).get_table_names()) - _NOT_MODELLED
    modelled = set(Base.metadata.tables)
    assert live == modelled, (
        f"schema drift — tables with no model: {sorted(live - modelled)}, "
        f"models with no table: {sorted(modelled - live)}"
    )


def test_every_model_column_matches_the_table(db: Path) -> None:
    inspector = inspect(get_engine(db))
    drift = {}
    for name, table in Base.metadata.tables.items():
        live = {column["name"] for column in inspector.get_columns(name)}
        modelled = set(table.columns.keys())
        if live != modelled:
            drift[name] = {
                "model_only": sorted(modelled - live),
                "table_only": sorted(live - modelled),
            }
    assert not drift, f"column drift: {drift}"


def test_singletons_are_seeded(db: Path) -> None:
    """`mood` and `self` must exist at row 1 on a fresh database.

    Every prompt injects both, and the writers used to assume the row was there.
    """
    from kith.infra.db.repositories import self_model

    assert self_model.get_mood(db)["label"], "mood row missing or blank after init"
    assert self_model.get_self(db) is not None, "self row missing after init"


def test_writing_a_singleton_survives_a_missing_row(db: Path) -> None:
    """Deleting the row must not make the writer explode.

    This is the exact failure that hid in production: UPDATE matched nothing, the
    follow-up SELECT returned None, and the row mapper raised TypeError.
    """
    from sqlalchemy import text

    from kith.infra.db.engine import session
    from kith.infra.db.repositories import self_model

    with session(db) as active:
        active.execute(text("DELETE FROM mood"))
        active.execute(text("DELETE FROM self"))

    assert self_model.set_mood(db, label="recovered", energy=42)["energy"] == 42
    assert self_model.set_self(db, identity="recovered")["identity"] == "recovered"
