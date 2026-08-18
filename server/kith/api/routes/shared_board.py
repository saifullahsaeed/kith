"""What a project's folder is holding that the board has not taken in.

`.kith/` travels with the repository, so a second person's tasks arrive as files and the board
is a per-machine database that has never read them. `services/board_sync` does the reading;
these two endpoints are how a person does it, rather than only the model.

Split from `roadmap.py` rather than added to it because they answer a different question about
the same project — the roadmap is what the work depends on, this is what has come in from
somebody else — and one file per question is why `roadmap.py` reads the way it does.
"""

from __future__ import annotations

from flask import jsonify

from kith.api.blueprint import api
from kith.infra.db import repositories as repo
from kith.services import board_sync
from kith.settings import AGENT_DB_PATH


def _folder(project_id: int) -> tuple[str, dict | None]:
    row = repo.projects.get_project(AGENT_DB_PATH, project_id)
    return str((row or {}).get("directory") or "").strip(), row


@api.get("/projects/<int:project_id>/shared")
@api.doc(
    summary="What this project's folder holds that the board does not",
    description=(
        "Reads `.kith/tasks/` and compares it with the board without changing either. "
        "`added` are tasks somebody else filed, `updated` are ones where their file is newer "
        "than this board, `kept` are ones where this board is newer. `blocked` says why the "
        "folder could not be read at all — a merge in progress, or conflict markers in a "
        "brief. `changed_ago` is how long since anything in the folder was written, because "
        "nothing arrives until somebody fetches."
    ),
)
def shared_state(project_id: int):
    directory, row = _folder(project_id)
    if row is None:
        return jsonify({"error": "no such project"}), 404
    if not directory:
        return jsonify(
            {"folder": "", "added": [], "updated": [], "kept": [], "blocked": "", "changed_ago": 0}
        )
    return jsonify({"folder": directory, **board_sync.preview(AGENT_DB_PATH, project_id, directory)})


@api.post("/projects/<int:project_id>/shared")
@api.doc(
    summary="Take in what the folder is holding",
    description=(
        "Applies what the GET describes. Nothing is deleted and nothing newer is overwritten: "
        "where both sides changed, the more recent wins, and a brief with no date on it never "
        "does. Refuses outright while the folder is mid-merge."
    ),
)
def take_shared_in(project_id: int):
    directory, row = _folder(project_id)
    if row is None:
        return jsonify({"error": "no such project"}), 404
    if not directory:
        return jsonify({"error": "this project has no folder, so there is nothing to read"}), 400
    return jsonify({"folder": directory, **board_sync.pull(AGENT_DB_PATH, project_id, directory)})
