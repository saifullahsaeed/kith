"""Reaching his person."""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo
from kith.tools.params import STR
from kith.tools.registry import tool


def _reach_out(path: Path, a: dict) -> dict:
    message = (a.get("message") or "").strip()
    if not message:
        raise ValueError("message is required")
    saved = repo.messages.add_message(path, message)
    return {"sent": True, "id": saved["id"], "note": "They'll see this when they next look."}


@tool(
    "reach_out",
    "Say something to your person on your own — it reaches them even when "
    "they're not looking at this. Use it when you actually have something "
    "worth telling them: a discovery, a question, a wall you hit, a thought "
    "that matters. It's yours to spend; spend it when it means something.",
    {"message": {**STR, "description": "What you want to tell them, in your own words."}},
    required=("message",),
)
def reach_out(path: Path, args: dict):
    return _reach_out(path, args)
