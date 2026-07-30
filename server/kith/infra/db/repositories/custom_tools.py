"""Tools he built for himself."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import delete, select

from kith.infra.db.engine import as_dict, session
from kith.infra.db.models import CustomTool
from kith.infra.db.support import utc_now_iso


def add_custom_tool(
    path: Path,
    name: str,
    description: str,
    code: str,
    parameters: dict | None = None,
    required: list[str] | None = None,
    language: str = "python",
) -> dict:
    """Create or replace a tool by name — rebuilding one keeps its original date."""
    with session(path) as db:
        row = db.get(CustomTool, name)
        if row is None:
            row = CustomTool(name=name, created_at=utc_now_iso(), code="", description="")
            db.add(row)
        row.description = description
        row.parameters = json.dumps(parameters or {})
        row.required = json.dumps(required or [])
        row.language = language
        row.code = code
        db.flush()
        return _decoded(row)


def get_custom_tool(path: Path, name: str) -> dict | None:
    with session(path) as db:
        row = db.get(CustomTool, name)
        return _decoded(row) if row else None


def list_custom_tools(path: Path) -> list[dict]:
    with session(path) as db:
        rows = db.scalars(select(CustomTool).order_by(CustomTool.name)).all()
        return [_decoded(row) for row in rows]


def delete_custom_tool(path: Path, name: str) -> bool:
    with session(path) as db:
        return db.execute(delete(CustomTool).where(CustomTool.name == name)).rowcount > 0


def _decoded(row: CustomTool) -> dict:
    """Parameters and required are stored as JSON text; callers expect real objects."""
    data = as_dict(row)
    data["parameters"] = json.loads(data["parameters"])
    data["required"] = json.loads(data["required"])
    return data
