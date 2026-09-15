"""What can answer a question about meaning here, and getting one that can.

The semantic tools — who calls this, where is it defined, what breaks if I rename it — need a
language server, and most machines have none. That is an ordinary condition rather than a
broken one, but until now the only way out of it was through a conversation: he noticed, he
offered, you approved, he installed. That works and costs a turn.

This is the other path. The settings page can ask what this folder is written in, what can
serve it, and install the one thing missing — for nothing, on any turn, without involving him
at all.

**No permission prompt on this route, deliberately.** The gate exists to stop *Kith* doing
things to your machine without asking. A person clicking Install in their own settings window
is the asking; putting a second dialog in front of that would be the app confirming that you
meant to press the button you pressed. His route through `install_language_support` still goes
through the gate, because there the actor is him.

It reports the folder it looked at. "This project needs pyright" is only useful next to which
project — and `base_dir()` follows the work rather than sitting at the workspace root, which is
the difference between an answer about the code you are looking at and an answer about an empty
folder.
"""

from __future__ import annotations

from flask import jsonify, request

from kith.api.blueprint import api
from kith.engine.code.lsp import install
from kith.engine.code.lsp.manager import CANDIDATES, manager


def _served(root):
    def check(family: str) -> bool:
        try:
            return manager.find_binary(family, root) is not None
        except Exception:
            return False

    return check


@api.get("/language-servers")
@api.doc(
    summary="Which languages this folder uses, and what can serve each one",
    description=(
        "Reports the folder inspected and one entry per language found in it: how many "
        "files, whether a server is already available, whether Kith can install one, and "
        "roughly how large it is. Languages whose servers come from their own package "
        "manager (Go, Rust, Ruby, C) are reported as not installable, with the command."
    ),
)
def language_servers_state():
    from kith.infra import workspace as sandbox

    root = sandbox.base_dir()
    served = _served(root)
    counted = install.languages_in(root)

    # One row per *server*, not per language.
    #
    # `languages_in` counts by language — `tsx`, `javascript` and `typescript` are three
    # different answers — and this reported each of them separately. So a project written in
    # TypeScript showed three rows, each offering a 32 MB download, for the one
    # `typescript-language-server` that serves all three. Installing from any of them satisfied
    # all three at once, which read as the button doing nothing.
    #
    # Grouped by the family that actually answers, with the languages it covers named so the
    # row still says why it is there.
    grouped: dict[str, dict] = {}
    for language, files in sorted(counted.items(), key=lambda pair: -pair[1]):
        if files < install.MIN_FILES:
            continue  # one stray file is not what this project is written in
        family = manager.family_of(language)
        entry = grouped.get(family)
        if entry is None:
            candidates = CANDIDATES.get(family, ())
            entry = grouped[family] = {
                "family": family,
                "languages": [],
                "files": 0,
                "served": served(family),
                # Asked of the *language* rather than the family: `install.PACKAGES` is keyed by
                # language, and a family name is not always one of its keys.
                "installable": bool(install.installable(language)),
                "size": install.download_size(language),
                # What to run by hand for the ones we do not install. The first candidate is
                # the preferred one, and its own table already carries the command.
                "manual": candidates[0].install if candidates else "",
            }
        entry["languages"].append(language)
        entry["files"] += files

    languages = sorted(grouped.values(), key=lambda one: -one["files"])
    return jsonify({"root": str(root), "languages": languages})


@api.post("/language-servers/install")
@api.doc(
    summary="Install the language server for one language",
    description=(
        "Body {family: 'python'}. Installs into Kith's own folder, never globally and never "
        "into the application bundle, and only from a closed table of packages — the family "
        "name selects a command, it never becomes one. Available on the next call with no "
        "restart."
    ),
)
def language_servers_install():
    body = request.get_json(silent=True) or {}
    family = str(body.get("family") or "").strip().lower()
    if not family:
        return jsonify({"error": "family is required"}), 400
    if not install.installable(family):
        return jsonify({"error": f"there is no server I install for {family}"}), 400

    worked, said = install.run(family)
    if not worked:
        return jsonify({"error": said}), 400
    return jsonify({"ok": True, "family": family, "where": said})
