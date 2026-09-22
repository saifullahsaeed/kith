"""Which design system a screen is drawn in, served to the interface.

A design references `var(--ds-accent)` and something has to say what that is. The renderer, the
inspector's swatches and the checker all need the same answer, and the one thing worse than no
system is three of them disagreeing.

**Why the server and not the interface.** The obvious place was the browser: fetch a
`design-system.json` from the workspace the way everything else is fetched. That works for a
system somebody wrote for screens and misses the one that already exists — the owner's brand is
in `brand/profile.json` *inside a skill*, where it has been since before this surface did, and
the workspace routes cannot see into the skills directory. The interface's answer was therefore
to copy it into the workspace, which is how two files come to disagree by next month.

So resolution happens here, where every folder is reachable, and the file stays where it was
authored. Nothing is copied and nothing goes stale.

Resolution, parsing and validation all live in :mod:`kith.services.design_system` — see that
module for the search order and for why the Python reader exists at all now. This route is the
HTTP shape over it. The raw text is still returned under `content`, because the interface parses
it itself and must keep doing so; the parsed view alongside it is what the interface cannot
cheaply work out, namely the component inventory and the conventions prose.
"""

from __future__ import annotations

from flask import jsonify, request

from kith.api.blueprint import api
from kith.services import design_system as systems


@api.get("/design-system")
@api.doc(
    summary="The design system a screen is drawn in",
    description=(
        "Finds the nearest design system — a project's folder, then the workspace, then the "
        "installed skills — and returns it as text for the caller to parse, alongside the "
        "component inventory and the conventions prose beside it. ?projectId= scopes the "
        "search to one project. Answers with an empty path when there is none, which is the "
        "ordinary case rather than an error."
    ),
)
def design_system():
    project_id = request.args.get("projectId")
    found = systems.find(project_id)
    if not found:
        return jsonify({"path": "", "content": "", "components": {}, "conventions": ""})

    path, text = found
    resolved = systems.resolve(project_id)
    return jsonify(
        {
            "path": str(path),
            "content": text,
            # What the interface would otherwise have to resolve for itself, and could not:
            # conventions live in a file beside the system, and finding it means knowing which
            # folder the system came from.
            "components": {name: part.as_dict() for name, part in resolved.components.items()},
            "conventions": resolved.conventions,
            "conventionsPath": str(resolved.conventions_path) if resolved.conventions_path else "",
            "shared": resolved.shared,
        }
    )
