"""Installing and removing skills.

Installing is a person's decision, not his. The Agent Skills documentation is blunt about
why: a skill is instructions plus executable code from somewhere else, and a malicious one
can direct an agent to use its tools for something other than the stated purpose. Kith gets
``read_skill`` and nothing else — he can use what was installed, and choosing what to trust
stays here, where there is a folder to look at first.

Nothing is fetched from the network by these routes either. A skill arrives as a folder on
disk that someone already has and has had the chance to read.
"""

from __future__ import annotations

from pathlib import Path

from flask import jsonify, request

from kith.api.blueprint import api
from kith.services import skills


@api.get("/skills")
@api.doc(
    summary="Installed skills",
    description=(
        "Every installed skill with its metadata, any folders that failed to parse and why, "
        "and what the whole set costs in prompt tokens — the last one because it is the "
        "number that decides whether installing another is free."
    ),
)
def list_skills():
    return jsonify(skills.snapshot())


@api.post("/skills/validate")
@api.doc(
    summary="Check a folder against the standard",
    description=(
        "Reports what is wrong with a candidate skill without installing it. Checked against "
        "the published spec rather than what happens to work here, so a skill Kith accepts "
        "is one other agents accept. Body: {path}."
    ),
)
def validate_skill():
    payload = request.get_json(silent=True) or {}
    directory = Path(str(payload.get("path") or "")).expanduser()
    if not directory.is_dir():
        return jsonify({"error": f"{directory} is not a folder."}), 400
    faults = skills.validate(directory)
    return jsonify(
        {
            "valid": not [f for f in faults if "(Warning only.)" not in f],
            "problems": [f for f in faults if "(Warning only.)" not in f],
            "warnings": [f.replace(" (Warning only.)", "") for f in faults if "(Warning only.)" in f],
        }
    )


@api.post("/skills")
@api.doc(
    summary="Install a skill from a folder",
    description=(
        "Copies a skill folder in after validating it, so a rejected one leaves nothing "
        "behind. Body: {path, overwrite}."
    ),
)
def install_skill():
    payload = request.get_json(silent=True) or {}
    try:
        installed = skills.install(
            Path(str(payload.get("path") or "")),
            overwrite=bool(payload.get("overwrite")),
        )
    except skills.SkillError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(installed.public())


@api.get("/skills/<name>")
@api.doc(
    summary="One skill's instructions",
    description="The SKILL.md body and the names of its bundled files — the same view he gets.",
)
def read_skill(name: str):
    try:
        return jsonify(skills.read(name))
    except skills.SkillError as exc:
        return jsonify({"error": str(exc)}), 404


@api.delete("/skills/<name>")
@api.doc(
    summary="Uninstall a skill",
    description=(
        "The folder goes to the Trash rather than being destroyed — someone who spent a "
        "fortnight editing a skill should be able to get it back."
    ),
)
def remove_skill(name: str):
    try:
        skills.remove(name)
    except skills.SkillError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"removed": name})
