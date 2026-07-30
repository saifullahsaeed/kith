"""The one API blueprint.

Separate from the package __init__ so every route module can import it while
that __init__ is still executing — importing the modules IS how they register.
"""

from __future__ import annotations

from apiflask import APIBlueprint

#: Mounted at /api by the app factory.
api = APIBlueprint("api", __name__)
