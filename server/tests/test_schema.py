"""The models must describe the schema the migrations actually build.

This is the test that matters most for the data layer. The ORM models are hand-
written to mirror `migrations.py` rather than to define it, so the two can drift —
and drift here does not raise on import or on a read. It surfaces as a write that
silently fails, or a column that is always None.

It used to also pin two singleton rows, `mood` and `self`. Both tables are gone —
see migration v37; the feature they backed was used twice in 14,327 tool calls. What
follows is the column-drift check, which is the part that still earns its place.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect

from kith.infra.db.engine import get_engine
from kith.infra.db.models import Base

# Tables the migrations create that no model needs to describe.
_NOT_MODELLED = {"sqlite_sequence"}

#: Tables that exist only because a migration once created them, and whose model has since
#: been deliberately removed.
#:
#: A migration cannot be unwritten — it is history, and editing one breaks every database
#: that already ran it — so a retired feature leaves its table behind on every existing
#: install. Dropping it in a new migration is possible but buys nothing: an empty table
#: nothing opens costs a few bytes, and a DROP is the one migration that cannot be undone if
#: the retirement turns out to be wrong.
#:
#: Naming them here rather than widening `_NOT_MODELLED` keeps the distinction that makes
#: this test worth having. A table nobody meant to leave unmodelled still fails; this list is
#: a decision, and adding to it is a deliberate act with a reason attached.
_RETIRED = {
    # The scheduled inner life: reflection every twentieth idle tick, curiosity every
    # thirtieth. Removed with the roaming loop — reflection is a note he writes when he has
    # something to say, not a mode a clock puts him in.
    "curiosities",
    # A layer between a project and its tasks, with a dependency graph deciding what he was
    # allowed to pick up. Removed for hiding available work more often than it ordered it —
    # 69 tasks on a real board pointed at milestones that no longer existed. A project is its
    # tasks now, and a task is its checklist.
    "milestones",
    "milestone_deps",
}

#: The same decision for a column: `(table, column)` pairs a migration added and no model reads
#: any more. Kept for the reason `_RETIRED` gives, and one more — a database this branch has run
#: against still opens under a checkout from before the change.
_RETIRED_COLUMNS = {
    ("tasks", "milestone_id"),
}


def test_every_table_has_a_model(db: Path) -> None:
    live = set(inspect(get_engine(db)).get_table_names()) - _NOT_MODELLED - _RETIRED
    modelled = set(Base.metadata.tables)
    assert live == modelled, (
        f"schema drift — tables with no model: {sorted(live - modelled)}, "
        f"models with no table: {sorted(modelled - live)}"
    )


def test_a_retired_table_is_really_gone_from_the_models(db: Path) -> None:
    """The other half, so `_RETIRED` cannot quietly become a way to ignore a live table.

    A name in that list must have no model. If someone brings the feature back, they add the
    model and remove the name in the same change, rather than having both and a test that
    shrugs.
    """
    both = _RETIRED & set(Base.metadata.tables)
    assert not both, f"listed as retired but still modelled: {sorted(both)}"


def test_a_retired_table_still_exists_in_the_schema(db: Path) -> None:
    """And that the name is not simply a typo nobody would ever notice."""
    live = set(inspect(get_engine(db)).get_table_names())
    missing = _RETIRED - live
    assert not missing, f"listed as retired but no migration creates them: {sorted(missing)}"


def test_every_model_column_matches_the_table(db: Path) -> None:
    inspector = inspect(get_engine(db))
    drift = {}
    for name, table in Base.metadata.tables.items():
        live = {column["name"] for column in inspector.get_columns(name)}
        live -= {column for owner, column in _RETIRED_COLUMNS if owner == name}
        modelled = set(table.columns.keys())
        if live != modelled:
            drift[name] = {
                "model_only": sorted(modelled - live),
                "table_only": sorted(live - modelled),
            }
    assert not drift, f"column drift: {drift}"


def test_a_retired_column_is_unmodelled_and_still_there(db: Path) -> None:
    """`_RETIRED_COLUMNS` held to the same two checks as `_RETIRED`: not modelled, and real."""
    inspector = inspect(get_engine(db))
    for owner, column in _RETIRED_COLUMNS:
        assert column not in Base.metadata.tables[owner].columns, f"{owner}.{column} is still modelled"
        assert column in {one["name"] for one in inspector.get_columns(owner)}, (
            f"no migration adds {owner}.{column}"
        )
