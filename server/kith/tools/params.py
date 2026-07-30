"""Shared JSON-schema fragments for tool parameters.

Every tool declares its arguments as JSON schema, and almost every argument is a
plain string or integer. Naming the two shapes once keeps 51 declarations from
repeating `{"type": "string"}` and makes a change (adding a description convention,
say) a one-line edit rather than a search-and-replace.
"""

from __future__ import annotations

STR: dict = {"type": "string"}
INT: dict = {"type": "integer"}
