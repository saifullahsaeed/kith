"""The configuration database.

A small key/value settings store (values are JSON, so types survive a round
trip). It holds the runtime chat defaults — model, context/output sizes, and
whether the model reasons — persisted so they outlive restarts and can be
changed at runtime without touching env vars or code. It's intentionally
schemaless-ish (key/value) so new settings can be added without a migration.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kith.infra.db.connection import apply_migrations, connect

# Seeded on first init; also the fallback if a key is ever missing.
DEFAULT_SETTINGS: dict[str, Any] = {
    "model": "qwen3:4b",
    "num_ctx": 40960,
    "num_predict": 8192,
    "think": True,
}


def _migrations():
    def v1_settings(conn):
        conn.execute(
            """
            CREATE TABLE settings (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,   -- JSON-encoded
                updated_at TEXT NOT NULL    -- ISO 8601, UTC
            )
            """
        )

    return [v1_settings]


def init(path: Path) -> None:
    """Create/upgrade the config database and seed any missing defaults."""
    conn = connect(path)
    try:
        apply_migrations(conn, _migrations())
        now = _now()
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                (key, json.dumps(value), now),
            )
        conn.commit()
    finally:
        conn.close()


def load_settings(path: Path) -> dict[str, Any]:
    """Read all settings as a plain dict (JSON-decoded values)."""
    conn = connect(path)
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {row["key"]: json.loads(row["value"]) for row in rows}
    finally:
        conn.close()


def update_settings(path: Path, updates: dict[str, Any]) -> dict[str, Any]:
    """Upsert the given settings, then return the full settings dict."""
    conn = connect(path)
    try:
        now = _now()
        for key, value in updates.items():
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, json.dumps(value), now),
            )
        conn.commit()
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {row["key"]: json.loads(row["value"]) for row in rows}
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(UTC).isoformat()
