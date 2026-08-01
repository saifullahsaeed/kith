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

import conftest
import pytest

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
    """The real one, not the temp folder the suite is redirected into.

    `settings.DATA_DIR` is a temp directory by the time any test runs — that is what keeps
    the suite out of the user's data. This test is about where the app puts things on a real
    machine, so it asserts on the value captured before the redirect.
    """
    assert conftest.REAL_DATA_DIR.name == "data"
    assert conftest.REAL_DATA_DIR.parent == settings.SERVER_ROOT


def test_directives_are_all_present() -> None:
    """Every directive the loop can dispatch to has a file behind it, and none is empty.

    A count rather than a list, and the count came down from eight: reflection, curiosity and
    consolidation were removed with the modes that used them. They were self-directed inner
    life on a timer — every twentieth idle tick a reflection, every thirtieth a curiosity —
    which is a strange thing to schedule and, in practice, was work happening on a board
    nobody was watching.

    An empty directive is the failure worth catching. It fails silently: the tick runs with
    no instruction at all and does something plausible-looking for reasons nobody can trace.
    """
    from kith.autonomy import directives

    assert set(directives.ALL) == {"autonomy", "breakout", "reply", "resume", "work"}
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


class TestEditingThePersona:
    """The persona is a folder of files, and these endpoints write to it.

    So the traversal guard matters as much as the editing does: a write endpoint pointed at
    a directory is exactly the shape that let `/etc/passwd` through the workspace routes
    once, by stripping slashes before checking containment instead of after.
    """

    def test_a_plain_name_lands_in_the_folder(self) -> None:
        from kith.services import persona

        resolved = persona._resolve("50-mine.md", must_exist=False)
        assert resolved.parent == persona.persona_dir().resolve()

    def test_traversal_is_refused(self) -> None:
        from kith.services import persona

        for name in ("../../../etc/passwd.md", "/etc/passwd.md", "sub/../../out.md"):
            with pytest.raises(persona.PersonaError):
                persona._resolve(name, must_exist=False)

    def test_only_markdown_and_text_are_fragments(self) -> None:
        from kith.services import persona

        with pytest.raises(persona.PersonaError):
            persona._resolve("evil.sh", must_exist=False)

    def test_the_title_drops_the_ordering_prefix(self) -> None:
        from kith.services import persona

        assert persona._title("40-how-you-work.md") == "how you work"
        assert persona._title("_45-how-you-read.md") == "how you read"

    def test_every_fragment_on_disk_is_listed_including_disabled_ones(self) -> None:
        """`fragment_paths` hides disabled ones on purpose — the loader wants what he IS.
        An editor wants what there is, or you cannot turn anything back on."""
        from kith.services import persona

        listed = {one["name"] for one in persona.fragments()}
        assert {path.name for path in persona.fragment_paths()} <= listed

    def test_the_read_discipline_fragment_is_present_and_active(self) -> None:
        """The habit that decides whether a long job finishes. Silence is its failure mode,
        so this asserts it is actually in the merged prompt rather than merely on disk."""
        from kith.services.persona import load_persona

        merged = load_persona()
        assert "How you read" in merged
        assert "grep" in merged

    def test_the_persona_no_longer_promises_a_linux_container(self) -> None:
        """He had one for the life of the sandbox. Telling him he still does is how he ends
        up reporting himself blocked on a path that cannot exist."""
        from kith.services.persona import load_persona

        merged = load_persona()
        assert "root on" not in merged
        assert "~/Kith" in merged
