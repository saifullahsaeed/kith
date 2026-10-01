"""A screen made of parts it references, rather than copies it pastes.

Measured 2026-10-01 across his real projects: a component was markup he pasted with a
`data-k-component` label, so the label named a part and the bytes drifted — ironline's
`button.primary` existed in 4 versions across 7 uses, sadeef-v2's `notification.row` in 4 across
6. On Pace (54 screens) he stopped using the format and wrote a Python generator to get reuse,
which made every hand edit in the editor last only until the next build.

So a part is a file beside the system, `components/<name>.html`, and a screen says
`<k-use name="row.detail" label="Checking">`. These tests hold three things to that:

* the service stores a part as a file and reads it back with what a screen needs to use it;
* `read_design_system` lists a file part by its tag and props, not its markup;
* the standalone checker expands a `<k-use>` exactly the way the editor does — byte for byte
  against `ui/src/lib/design/expand.fixture.json`, which `compose.test.ts` also asserts — because
  the bug this surface keeps producing is two renderers drawing two screens from one file.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from kith import settings
from kith.services import design_system as systems

FIXTURE = json.loads(
    (settings.SERVER_ROOT.parent / "ui" / "src" / "lib" / "design" / "expand.fixture.json").read_text()
)

ROW = '<div data-k="row" style="display: flex; gap: 12px"><strong>{{label}}</strong><span>{{value}}</span></div>'


@pytest.fixture(autouse=True)
def writes_are_allowed(monkeypatch):
    """The gate is tested in `test_a_gate_nobody_is_at.py`; here it would only park the thread."""
    from kith.infra import permissions

    monkeypatch.setattr(permissions, "mode", lambda: permissions.Mode.BYPASS)


@pytest.fixture(autouse=True)
def removals_stay_out_of_the_real_trash(monkeypatch):
    """Removing a part removes its file, through the Trash. Not his Trash, from a test."""
    from kith.infra.workspace import files

    monkeypatch.setattr(files, "trash_path", lambda target: (target.unlink(), "gone")[1])


@pytest.fixture
def project(tmp_path: Path) -> int:
    from kith.infra.db import repositories as repo

    folder = tmp_path / "pace"
    folder.mkdir(parents=True, exist_ok=True)
    made = repo.projects.add_project(settings.AGENT_DB_PATH, "Pace", directory=str(folder))
    return made["id"] if isinstance(made, dict) else made


def _system_file(project_id: int) -> Path:
    from kith.infra.db import repositories as repo

    return (
        Path(repo.projects.get_project(settings.AGENT_DB_PATH, project_id)["directory"]) / systems.CANONICAL
    )


@pytest.fixture
def checker():
    script = settings.SERVER_ROOT / "skills" / "designing-a-screen" / "scripts" / "check_design.py"
    if "check_design_under_test" in sys.modules:
        return sys.modules["check_design_under_test"]
    spec = importlib.util.spec_from_file_location("check_design_under_test", script)
    assert spec is not None and spec.loader is not None, script
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # playwright missing is a real answer, not a hidden skip
        pytest.skip(f"the checker could not be imported: {exc}")
    sys.modules[spec.name] = module
    return module


class TestAPartIsAFile:
    def test_saving_a_part_writes_its_own_file_with_its_note(self, project):
        systems.save(
            project,
            colors={"ink": "#111111"},
            components={"row.detail": {"html": ROW, "use": "A label and its value."}},
        )

        part = _system_file(project).parent / "components" / "row.detail.html"
        assert part.is_file(), "a part lands beside the system, one file each"
        assert part.read_text().startswith("<!-- use: A label and its value. -->\n")
        doc = json.loads(_system_file(project).read_text())
        assert "row.detail" not in doc.get("components", {}), "the JSON does not keep a second copy"

    def test_it_reads_back_with_what_a_screen_needs_to_use_it(self, project):
        systems.save(
            project, colors={"ink": "#111111"}, components={"row.detail": {"html": ROW, "use": "A row."}}
        )

        part = systems.resolve(project).components["row.detail"]
        assert part.html == ROW
        assert part.use == "A row."
        assert part.props == ["label", "value"]
        assert part.slot is False
        assert part.path == _system_file(project).parent / "components" / "row.detail.html"

    def test_an_inline_part_from_an_older_system_still_reads_and_a_file_wins(self, project):
        target = _system_file(project)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "colors": {"ink": "#111"},
                    "components": {
                        "chip": {"html": "<span>old</span>", "use": "inline"},
                        "row.detail": {"html": "<div>stale copy</div>"},
                    },
                }
            )
        )
        (target.parent / "components").mkdir()
        (target.parent / "components" / "row.detail.html").write_text(ROW)

        parts = systems.resolve(project).components
        assert parts["chip"].html == "<span>old</span>" and parts["chip"].path is None
        assert parts["row.detail"].html == ROW, (
            "the file is the part; an inline entry of the same name is not"
        )

    def test_a_part_with_a_script_is_refused_with_the_reason(self, project):
        with pytest.raises(systems.SystemError, match="refused"):
            systems.save(
                project, colors={"ink": "#111"}, components={"x": {"html": "<div><script>x()</script></div>"}}
            )

    def test_a_hole_named_after_the_tags_own_attribute_is_refused(self, project):
        """`<k-use name="…">` says which part, so a `{{name}}` hole could never be filled."""
        with pytest.raises(systems.SystemError, match="cannot be a hole"):
            systems.save(
                project, colors={"ink": "#111"}, components={"card": {"html": "<div>{{name}}</div>"}}
            )

    def test_null_removes_the_part_and_its_file(self, project):
        systems.save(project, colors={"ink": "#111"}, components={"row.detail": {"html": ROW}})
        systems.save(project, components={"row.detail": None})

        assert "row.detail" not in systems.resolve(project).components
        assert not (_system_file(project).parent / "components" / "row.detail.html").exists()


class TestReadingTheInventory:
    def test_a_file_part_is_listed_by_its_tag_and_not_its_markup(self, project, monkeypatch):
        from kith.tools import design as tools

        systems.save(
            project, colors={"ink": "#111"}, components={"row.detail": {"html": ROW, "use": "A row."}}
        )
        out = tools.read_design_system(Path("."), {"project": project})

        (listed,) = out["components"]
        assert listed["write"] == '<k-use name="row.detail" label="…" value="…"></k-use>'
        assert listed["props"] == ["label", "value"]
        assert "html" not in listed, (
            "the markup is read from the file when changing the part, not on every read"
        )


class TestTheCheckerExpandsTheWayTheEditorDoes:
    @pytest.mark.parametrize("case", FIXTURE["cases"], ids=[one["why"][:60] for one in FIXTURE["cases"]])
    def test_byte_for_byte(self, checker, case):
        assert checker._expand(case["input"], FIXTURE["parts"]) == case["output"]

    def test_it_reads_part_files_beside_the_system(self, checker, tmp_path):
        system = tmp_path / ".kith" / "design" / "system.json"
        (system.parent / "components").mkdir(parents=True)
        system.write_text(json.dumps({"colors": {"ink": "#111"}}))
        (system.parent / "components" / "row.detail.html").write_text("<!-- use: A row. -->\n" + ROW)

        read = checker._read_system(system)
        assert read["components"]["row.detail"]["html"] == ROW

    def test_a_used_part_counts_and_a_missing_one_fails(self, checker):
        used, unknown = checker._parts(
            '<k-use name="row.detail"></k-use><k-use name="nope" />', {"row.detail": {}}
        )
        assert used == ["nope", "row.detail"]
        assert unknown == ["nope"]
