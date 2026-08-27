"""Embedding storage and comparison. Vectors are packed to bytes for SQLite and
compared by cosine similarity — the whole of Kith's semantic recall maths."""

from __future__ import annotations

from array import array
from math import sqrt


def pack_vector(vec: list[float] | None) -> bytes | None:
    """Serialise a float vector to compact float32 bytes (None stays None)."""
    return array("f", vec).tobytes() if vec else None


def unpack_vector(blob: bytes | None) -> array:
    """Read a packed vector back. A row with no vector yields an empty one, which `cosine`
    scores at 0 — "never embedded" and "no similarity" want the same answer here."""
    vec = array("f")
    if blob:
        vec.frombytes(blob)
    return vec


def cosine(a, b) -> float:
    """Cosine similarity of two equal-length float sequences; 0 if either is empty."""
    if len(a) != len(b) or not a:
        return 0.0
    # strict is safe: the length guard above has already returned for mismatches.
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sqrt(sum(x * x for x in a))
    norm_b = sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
