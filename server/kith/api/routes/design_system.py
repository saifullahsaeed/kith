"""Which design system a screen is drawn in, resolved in one place.

A design references `var(--ds-accent)` and something has to say what that is. Three things need
the same answer — the renderer that draws the design, the inspector that offers its swatches, and
the checker that says whether a screen obeys it — and the one thing worse than no system is three
of them disagreeing.

**Why the server and not the interface.** The obvious place was the browser: fetch a
`design-system.json` from the workspace the way everything else is fetched. That works for a
system somebody wrote for screens and misses the one that already exists — the owner's brand is
in `brand/profile.json` *inside a skill*, where it has been since before this surface did, and
the workspace routes cannot see into the skills directory. The interface's answer was therefore
to copy it into the workspace, which is how two files come to disagree by next month.

So resolution happens here, where every folder is reachable, and the file stays where it was
authored. Nothing is copied and nothing goes stale.

**Order, and why.** A project's own folder first, because a design system belongs to a product
and he works on several; then the workspace root, for a person with one; then the installed
skills, which is where a brand authored for something else (a deck, a report) already lives. A
file written *for screens* outranks one adapted *into* them, so within each folder the search
tries `design-system.json` before `brand/profile.json`.

The file is returned as text. It is not parsed here: the interface already has both readers, with
tests, and the checker has its own — a third parser in Python would be a third opinion about what
a token means.
"""

from __future__ import annotations

from pathlib import Path

from flask import jsonify, request

from kith.api.blueprint import api
from kith.infra import workspace as sandbox
from kith.infra.db import repositories as repo
from kith.services import skills
from kith import settings

#: Within a folder, in order. A system authored for screens beats one adapted from slides.
NAMES = ("design-system.json", "brand/design-system.json", "design/system.json", "brand/profile.json")

#: Enough to be a system rather than any JSON that happens to sit at the path.
def _is_system(text: str) -> bool:
    import json

    try:
        doc = json.loads(text)
    except ValueError:
        return False
    if not isinstance(doc, dict):
        return False
    colours = doc.get("colors") or (doc.get("tokens") or {}).get("colors")
    return isinstance(colours, dict) and bool(colours)


def _folders(project_id: str | None) -> list[Path]:
    """Where to look, nearest first."""
    places: list[Path] = []

    if project_id:
        try:
            # Read live rather than bound at import: this module is imported while the app is
            # being created, which is after anything that redirects the database has run, so a
            # captured constant here is the pre-redirect path.
            project = repo.projects.get_project(settings.AGENT_DB_PATH, int(project_id))
            directory = str((project or {}).get("directory") or "").strip()
            if directory:
                places.append(Path(directory).expanduser())
        except Exception:
            # A project whose folder cannot be read is not a reason to have no system at all.
            pass

    try:
        places.append(sandbox.root())
    except Exception:
        pass

    # Installed skills, which is where a brand profile authored for something else already lives.
    try:
        for skill in sorted(skills.root().iterdir()):
            if skill.is_dir():
                places.append(skill)
    except OSError:
        pass

    return places


@api.get("/design-system")
@api.doc(
    summary="The design system a screen is drawn in",
    description=(
        "Finds the nearest design system — a project's folder, then the workspace, then the "
        "installed skills — and returns it as text for the caller to read. ?projectId= scopes "
        "the search to one project. Answers with an empty path when there is none, which is the "
        "ordinary case rather than an error."
    ),
)
def design_system():
    for folder in _folders(request.args.get("projectId")):
        for name in NAMES:
            candidate = folder / name
            try:
                if not candidate.is_file():
                    continue
                text = candidate.read_text(encoding="utf-8")
            except OSError:
                continue
            if _is_system(text):
                return jsonify({"path": str(candidate), "content": text})
    return jsonify({"path": "", "content": ""})
