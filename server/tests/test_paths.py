"""On-disk locations must survive a file being moved.

Modules used to walk up from their own ``__file__`` to find things — the persona
directory, the workspace folder. That works until the module moves into a
subpackage, at which point the path silently resolves somewhere empty. It happened:
moving ``persona.py`` into ``services/`` made Kith start with NO personality, and
the only sign was a "0 fragment(s)" line in a startup log nobody was reading.

These are cheap assertions about things that must exist. They are here because the
failure mode is silence, not an exception.
"""

from __future__ import annotations

from kith import settings
from kith.infra import workspace
from kith.services.persona import fragment_paths, load_persona


def test_server_root_is_the_server_directory() -> None:
    """Everything else is derived from this, so it has to be right."""
    assert (settings.SERVER_ROOT / "app.py").is_file()
    assert (settings.SERVER_ROOT / "kith").is_dir()


def test_persona_fragments_are_found() -> None:
    """An empty persona is the failure this file exists for — he has no self without it."""
    fragments = fragment_paths()
    assert fragments, "no persona fragments found — Kith would start with no personality"
    assert all(path.suffix == ".md" for path in fragments)


def test_persona_actually_loads_text() -> None:
    """Paths existing is not enough; the merged prompt must have content."""
    assert len(load_persona()) > 200


def test_the_workspace_is_created_on_demand() -> None:
    """His folder replaced a Docker container, so nothing builds it in advance — the
    first call has to bring it into existence or every file tool fails at once."""
    here = workspace.root()
    assert here.is_dir()
    assert workspace.internal().is_dir()


def test_a_relative_path_lands_in_the_workspace() -> None:
    """The anchor that keeps "write notes.md" from writing to the server's cwd."""
    assert workspace.resolve("notes.md") == str(workspace.root() / "notes.md")
    assert workspace.resolve("") == str(workspace.root())


def test_an_absolute_path_is_kept_not_rewritten() -> None:
    """Refusing them here would make "read ~/Downloads/report.pdf" impossible rather
    than merely gated; the permission check is the gate, and it can be answered."""
    assert workspace.resolve("/etc/hosts") == "/etc/hosts"


def test_data_dir_resolves_under_the_server() -> None:
    assert settings.DATA_DIR.name == "data"
    assert settings.DATA_DIR.parent == settings.SERVER_ROOT


def test_directives_are_all_present() -> None:
    """Eight markdown files the autonomy loop reads at import."""
    from kith.autonomy import directives

    assert len(directives.ALL) == 8
    for name, text in directives.ALL.items():
        assert text.strip(), f"directive {name} is empty"


def test_no_module_derives_a_shared_path_from_its_own_location() -> None:
    """The rule this file protects, checked directly.

    ``settings`` may look at its own ``__file__`` — it is the anchor. Anything else
    walking upwards with ``parent.parent`` is one refactor away from pointing at the
    wrong directory, silently.
    """
    import ast
    import pathlib

    offenders = []
    root = pathlib.Path(settings.__file__).parent
    for path in root.rglob("*.py"):
        if "__pycache__" in str(path) or path.name == "settings.py":
            continue
        text = path.read_text()
        if "__file__" not in text:
            continue
        for node in ast.walk(ast.parse(text)):
            # .parent.parent — walking out of your own package to find a sibling tree
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "parent"
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "parent"
            ):
                offenders.append(str(path.relative_to(root)))
                break

    assert not offenders, (
        f"these walk up from __file__ and will break when moved: {sorted(set(offenders))}. "
        "Derive the path from settings.SERVER_ROOT instead."
    )
