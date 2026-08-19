"""Shared JSON-schema fragments for tool parameters, and how to read them back.

Every tool declares its arguments as JSON schema, and almost every argument is a plain
string or integer. Naming the shapes once keeps 60-odd declarations from repeating
`{"type": "string"}` and makes a change (adding a description convention, say) a one-line
edit rather than a search-and-replace.

:data:`LIST_STR` declares the plural shape; ``domain.tooling.many`` reads it back. They are a
pair and live apart for one reason — `services/touched.py` has to read the same argument to
know which files a batched read touched, and a service reaching into the tool layer is the
arrow pointing the wrong way. The reader is therefore in domain, where both may take it.
"""

from __future__ import annotations

STR: dict = {"type": "string"}
INT: dict = {"type": "integer"}
BOOL: dict = {"type": "boolean"}

#: One or more strings. The declared shape of every tool that can do its job to several things
#: at once — reading files, grepping patterns, outlining modules.
LIST_STR: dict = {"type": "array", "items": {"type": "string"}}
