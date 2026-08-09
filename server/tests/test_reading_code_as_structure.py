"""Outlines and repo maps: knowing what is in code without reading it.

The point of both is his attention. A 2,000-line module read to find one function is that
module carried on every remaining round of the turn, and the persona can only ask him to be
careful about it — these give him a cheaper way to find out, which is the thing that actually
changes behaviour.

So the assertions that matter are about *compression* and *honesty*: the outline is a
fraction of the file, the map fits its budget, and anything left out is counted out loud
rather than silently dropped.
"""

from __future__ import annotations

import pytest

from kith.services.code import outline, repomap

PY_SOURCE = '''\
"""A module."""

CONSTANT = 1


def top_level(a, b=2):
    return a + b


class Thing:
    """A class."""

    def method(self, x):
        return x

    def other(self):
        pass


async def later():
    pass
'''

TS_SOURCE = """\
export const TABLE: Record<string, number> = { a: 1 };
const local = 5;

export interface Shape {
  width: number;
}

export function build(input: Shape): string {
  return String(input.width);
}

export const arrow = (n: number) => n * 2;

export class Widget {
  render(): string {
    return "";
  }
}
"""


class TestOutliningOneFile:
    def test_python_definitions_are_found_with_their_lines(self, tmp_path):
        path = tmp_path / "m.py"
        path.write_text(PY_SOURCE)

        found = outline.of_file(path)
        names = [one["name"] for one in found["symbols"]]

        assert found["language"] == "python"
        assert names == ["top_level", "Thing", "method", "other", "later"]
        # The line is what he passes to read_file as an offset, so it has to be right.
        by_name = {one["name"]: one for one in found["symbols"]}
        assert PY_SOURCE.splitlines()[by_name["top_level"]["line"] - 1].startswith("def top_level")
        assert PY_SOURCE.splitlines()[by_name["method"]["line"] - 1].strip().startswith("def method")

    def test_methods_are_nested_under_their_class(self, tmp_path):
        path = tmp_path / "m.py"
        path.write_text(PY_SOURCE)

        by_name = {one["name"]: one for one in outline.of_file(path)["symbols"]}

        assert by_name["Thing"]["depth"] == 0
        assert by_name["method"]["depth"] == 1
        assert by_name["top_level"]["depth"] == 0

    def test_a_plain_constant_is_not_a_symbol(self, tmp_path):
        """Otherwise every module-level assignment is a definition and the outline is noise."""
        path = tmp_path / "m.py"
        path.write_text(PY_SOURCE)
        assert "CONSTANT" not in [one["name"] for one in outline.of_file(path)["symbols"]]

    def test_typescript_finds_the_shapes_that_matter(self, tmp_path):
        path = tmp_path / "m.ts"
        path.write_text(TS_SOURCE)

        names = [one["name"] for one in outline.of_file(path)["symbols"]]

        assert "build" in names, "a plain exported function"
        assert "Shape" in names, "an interface"
        assert "Widget" in names, "a class"
        assert "arrow" in names, "const x = () => … is how most functions are written now"
        assert "TABLE" in names, "an exported lookup table is the file's API"
        assert "local" not in names, "a private local const is not a definition"

    def test_a_jsx_element_is_not_mistaken_for_a_definition(self, tmp_path):
        """Walking for any node with a name field lists every tag in the render tree, which
        is confidently wrong about what the file contains."""
        path = tmp_path / "c.tsx"
        path.write_text("export function Card() {\n  return <div><span/></div>;\n}\n")

        names = [one["name"] for one in outline.of_file(path)["symbols"]]

        assert "Card" in names
        assert "div" not in names and "span" not in names

    def test_a_parameter_is_not_a_definition(self, tmp_path):
        path = tmp_path / "m.py"
        path.write_text("def f(alpha, beta=1):\n    pass\n")
        names = [one["name"] for one in outline.of_file(path)["symbols"]]
        assert names == ["f"]

    def test_the_signature_is_the_declaration_line(self, tmp_path):
        path = tmp_path / "m.py"
        path.write_text("def f(a: int, b: str = 'x') -> bool:\n    return True\n")
        assert outline.of_file(path)["symbols"][0]["signature"] == "def f(a: int, b: str = 'x') -> bool"

    def test_an_outline_is_a_fraction_of_the_file(self, tmp_path):
        """The whole reason it exists. If it is not much smaller it has bought nothing."""
        path = tmp_path / "big.py"
        path.write_text("\n\n".join(f"def function_{i}(a, b):\n    " + "x = 1\n    " * 40 for i in range(40)))

        rendered = outline.render(outline.of_file(path))

        assert len(rendered) < len(path.read_text()) / 10


class TestFilesThatCannotBeOutlined:
    def test_an_unknown_extension_says_so_and_suggests_the_alternative(self, tmp_path):
        path = tmp_path / "notes.xyz"
        path.write_text("whatever")
        with pytest.raises(outline.OutlineError) as caught:
            outline.of_file(path)
        assert "read_file" in str(caught.value) or "grep" in str(caught.value)

    def test_a_binary_file_is_refused_rather_than_parsed(self, tmp_path):
        path = tmp_path / "thing.py"
        path.write_bytes(b"\x00\x01\x02\x03 not text")
        with pytest.raises(outline.OutlineError) as caught:
            outline.of_file(path)
        assert "binary" in str(caught.value)

    def test_a_huge_file_is_refused_with_a_reason(self, tmp_path, monkeypatch):
        monkeypatch.setattr(outline, "MAX_BYTES", 100)
        path = tmp_path / "gen.py"
        path.write_text("x = 1\n" * 200)
        with pytest.raises(outline.OutlineError) as caught:
            outline.of_file(path)
        assert "grep" in str(caught.value)

    def test_a_missing_file_says_it_cannot_open_it(self, tmp_path):
        with pytest.raises(outline.OutlineError):
            outline.of_file(tmp_path / "nope.py")

    def test_a_file_with_no_definitions_is_not_an_error(self, tmp_path):
        path = tmp_path / "data.py"
        path.write_text("x = 1\ny = 2\n")
        assert outline.of_file(path)["symbols"] == []
        assert "no definitions" in outline.render(outline.of_file(path))

    def test_a_syntax_error_still_yields_what_parsed(self, tmp_path):
        """tree-sitter recovers from errors, and a half-broken file is exactly when he is
        looking at it."""
        path = tmp_path / "broken.py"
        path.write_text("def works():\n    pass\n\nclass Unclosed(\n")
        assert "works" in [one["name"] for one in outline.of_file(path)["symbols"]]


class TestMappingARepository:
    @pytest.fixture
    def project(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("def run():\n    pass\n")
        (tmp_path / "src" / "auth.py").write_text("def login(user):\n    pass\n\ndef logout():\n    pass\n")
        (tmp_path / "src" / "billing.py").write_text("def invoice():\n    pass\n")
        # Things a map must never wander into.
        (tmp_path / "node_modules" / "dep").mkdir(parents=True)
        (tmp_path / "node_modules" / "dep" / "index.js").write_text("function nope() {}\n")
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "hook.py").write_text("def hidden():\n    pass\n")
        return tmp_path

    def test_it_finds_the_source_and_skips_the_dependencies(self, project):
        mapped = repomap.build(project)
        paths = " ".join(one["path"] for one in mapped["entries"])

        assert "main.py" in paths and "auth.py" in paths
        assert "node_modules" not in paths, "mapping a dependency tree describes none of his code"
        assert ".git" not in paths

    def test_the_rendered_map_names_the_definitions(self, project):
        text = repomap.render(repomap.build(project))
        assert "login" in text and "invoice" in text

    def test_focus_ranks_the_matching_files_first(self, project):
        mapped = repomap.build(project, focus="billing")
        assert mapped["entries"][0]["path"].endswith("billing.py")

    def test_it_stays_inside_its_budget(self, project):
        for _ in range(60):
            pass
        for i in range(60):
            (project / "src" / f"extra_{i}.py").write_text(
                "\n".join(f"def function_{j}(a, b, c):\n    pass\n" for j in range(20))
            )

        small = repomap.render(repomap.build(project, budget_tokens=400))
        large = repomap.render(repomap.build(project, budget_tokens=4000))

        assert len(small) < 400 * 3.7 * 1.6, "the budget is the design; a map that ignores it is the repo"
        assert len(large) > len(small)

    def test_what_it_left_out_is_said_out_loud(self, project):
        for i in range(40):
            (project / "src" / f"extra_{i}.py").write_text("def thing():\n    pass\n")

        mapped = repomap.build(project, budget_tokens=300)
        text = repomap.render(mapped)

        assert mapped["files_found"] > mapped["files_shown"]
        assert "more source files not shown" in text, (
            "a truncated map that looks whole is how you conclude a function does not exist"
        )

    def test_one_enormous_file_cannot_eat_the_whole_map(self, project):
        (project / "src" / "god.py").write_text(
            "class God:\n" + "\n".join(f"    def method_{i}(self):\n        pass\n" for i in range(200))
        )

        mapped = repomap.build(project)
        god = next(one for one in mapped["entries"] if one["path"].endswith("god.py"))

        assert len(god["symbols"]) <= repomap.MAX_SYMBOLS_PER_FILE + 1
        assert god["hidden"] > 0
        assert "more in this file" in repomap.render(mapped)

    def test_top_level_definitions_are_preferred_over_methods(self, project):
        """A map listing six methods of one class while omitting four sibling classes has
        described the file badly."""
        (project / "src" / "mixed.py").write_text(
            "class A:\n"
            + "\n".join(f"    def m{i}(self):\n        pass\n" for i in range(20))
            + "\ndef sibling_one():\n    pass\n\ndef sibling_two():\n    pass\n"
        )

        mapped = repomap.build(project, focus="mixed")
        entry = next(one for one in mapped["entries"] if one["path"].endswith("mixed.py"))
        names = [one["name"] for one in entry["symbols"]]

        assert "sibling_one" in names and "sibling_two" in names

    def test_a_folder_with_no_source_says_so(self, tmp_path):
        (tmp_path / "readme.txt").write_text("hello")
        with pytest.raises(repomap.RepoMapError) as caught:
            repomap.build(tmp_path)
        assert "list_files" in str(caught.value)

    def test_a_file_is_not_a_folder(self, tmp_path):
        path = tmp_path / "one.py"
        path.write_text("def f():\n    pass\n")
        with pytest.raises(repomap.RepoMapError):
            repomap.build(path)

    def test_a_symlink_loop_does_not_hang_it(self, tmp_path):
        (tmp_path / "a.py").write_text("def f():\n    pass\n")
        loop = tmp_path / "loop"
        try:
            loop.symlink_to(tmp_path, target_is_directory=True)
        except OSError:
            pytest.skip("no symlink support here")
        mapped = repomap.build(tmp_path)
        assert mapped["files_found"] >= 1


class TestTheTools:
    """Registered, permission-checked, and returning something a model can act on."""

    @pytest.fixture
    def workspace_root(self, tmp_path, monkeypatch):
        from kith.infra import workspace

        monkeypatch.setattr(workspace.paths, "configured_root", lambda: tmp_path)
        return tmp_path

    def test_outline_runs_through_the_registry(self, workspace_root, tmp_path):
        from kith.tools import registry

        (workspace_root / "m.py").write_text(PY_SOURCE)
        result = registry.get("outline").run(tmp_path / "agent.db", {"path": "m.py"})

        assert result["definitions"] == 5
        assert "class Thing" in result["outline"]

    def test_repo_map_runs_through_the_registry(self, workspace_root, tmp_path):
        from kith.tools import registry

        (workspace_root / "a.py").write_text("def alpha():\n    pass\n")
        result = registry.get("repo_map").run(tmp_path / "agent.db", {})

        assert result["filesShown"] >= 1
        assert "alpha" in result["map"]

    def test_an_unreadable_file_comes_back_as_an_error_not_an_exception(self, workspace_root, tmp_path):
        """A tool that raises ends the round; one that explains lets him try something else."""
        from kith.tools import registry

        (workspace_root / "notes.xyz").write_text("hello")
        result = registry.get("outline").run(tmp_path / "agent.db", {"path": "notes.xyz"})

        assert "error" in result

    def test_the_budget_cannot_be_driven_to_something_absurd(self, workspace_root, tmp_path):
        from kith.tools import registry

        (workspace_root / "a.py").write_text("def alpha():\n    pass\n")
        result = registry.get("repo_map").run(tmp_path / "agent.db", {"budget_tokens": 10_000_000})

        assert len(result["map"]) < 12_000 * 3.7 * 1.6
