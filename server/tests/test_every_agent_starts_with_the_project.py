"""Every workflow agent starts knowing the project it is working in, as a Claude subagent starts
with the project's CLAUDE.md.

Before this an agent was handed the persona, its role's brief and the prompt — nothing about the
project. Builders spent most of their rounds finding their way ("I ran out of my tool budget while
reading"), and the scripts Kith wrote pasted the same 400–680-word preamble into every prompt to
make up for it. The board is left out on purpose: which tasks are open is the main agent's
business, and an agent that reads a task list starts planning.
"""

from __future__ import annotations

from pathlib import Path

from kith.infra.db import repositories as repo
from kith.services import agent_loop
from kith.tools import workers


def _project(db: Path, tmp_path: Path, memory: str) -> Path:
    folder = tmp_path / "shop"
    (folder / ".kith").mkdir(parents=True)
    (folder / ".kith" / "memory.md").write_text(memory)
    (folder / ".kith" / "references.md").write_text("# References\n\n- The API spec is docs/api.md\n")
    made = repo.projects.add_project(db, "Shop", "An online shop.", directory=str(folder))
    repo.tasks.add_task(db, "SECRET-TASK-TITLE", project_id=int(made["id"]))
    return folder


class TestTheProjectBrief:
    def test_it_carries_the_project_its_references_and_its_memory(self, db: Path, tmp_path: Path):
        _project(db, tmp_path, "# Memory\n\nTabs, never spaces. CONVENTION-XYZ.\n")
        brief = workers.project_brief(db, "")
        assert "Shop" in brief and "An online shop." in brief
        assert "CONVENTION-XYZ" in brief
        assert "docs/api.md" in brief

    def test_it_leaves_the_board_out(self, db: Path, tmp_path: Path):
        _project(db, tmp_path, "# Memory\n")
        assert "SECRET-TASK-TITLE" not in workers.project_brief(db, "")

    def test_a_long_memory_is_cut_and_says_where_the_rest_is(self, db: Path, tmp_path: Path):
        _project(db, tmp_path, "# Memory\n\n" + ("a line of what he learned\n" * 2000))
        brief = workers.project_brief(db, "")
        assert len(brief) < workers.PROJECT_MEMORY_CHARS + 2_000
        assert ".kith/memory.md" in brief and "more characters" in brief

    def test_outside_a_project_there_is_nothing(self, db: Path):
        assert workers.project_brief(db, "") == ""


class TestItRidesInTheSystemPrompt:
    def test_the_agent_is_handed_it_after_its_brief(self, db: Path, monkeypatch):
        seen: list[list[dict]] = []

        def fake(convo, config, host, tools=None, tool_choice="auto", routing=None):
            seen.append([dict(m) for m in convo])
            yield {"type": "delta", "role": "text", "text": "ok"}
            yield {"type": "turn", "content": "ok", "tool_calls": [], "stats": {}}

        monkeypatch.setattr(agent_loop, "_stream_once", fake)
        workers.run_agent(db, "scout", "look", "", context="[The project you are working in: Shop]")
        system = str(seen[0][0]["content"])
        assert system.index(workers.SCOUT_BRIEF[:40]) < system.index("[The project you are working in: Shop]")
