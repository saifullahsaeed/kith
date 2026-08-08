"""SQLAlchemy engine and session, alongside the raw-SQL path during the swap.

Engines are cached per database path. Creating one per call would throw away the
connection pool on every query, and Kith opens a lot of short transactions.

The ``session`` context manager mirrors ``session.transaction`` exactly — commit
on success, roll back on failure, always close — so a repository can be moved
from raw SQL to the ORM without changing how it handles errors, and both styles
can coexist while the conversion is in flight.

Pragmas match ``connection.connect``: WAL so a reader never blocks the writer,
and a busy timeout so a second process waits its turn instead of failing. That
matters more than it looks — the scheduler and the API write concurrently.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

_engines: dict[str, Engine] = {}
_factories: dict[str, sessionmaker[Session]] = {}


def _configure(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA busy_timeout = 5000")
        cursor.close()


def get_engine(path: Path) -> Engine:
    key = str(path)
    if key not in _engines:
        path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(f"sqlite+pysqlite:///{path}", future=True)
        _configure(engine)
        _engines[key] = engine
        _factories[key] = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return _engines[key]


@contextmanager
def session(path: Path) -> Iterator[Session]:
    """One short unit of work. Commits on success, rolls back on any exception."""
    get_engine(path)
    with _factories[str(path)]() as active:
        try:
            yield active
            active.commit()
        except Exception:
            active.rollback()
            raise


def as_dict(row: object, *, drop: tuple[str, ...] = ()) -> dict:
    """A model instance as the plain dict the API and tool results expect.

    Repositories have always returned plain dicts, and everything downstream — the
    HTTP layer, the tool results the model reads — depends on that. Keeping it
    means the ORM stays an implementation detail of this layer.
    """
    mapper = getattr(type(row), "__mapper__", None)
    if mapper is None:
        raise TypeError(f"not a mapped instance: {type(row)!r}")
    return {column.key: getattr(row, column.key) for column in mapper.column_attrs if column.key not in drop}
