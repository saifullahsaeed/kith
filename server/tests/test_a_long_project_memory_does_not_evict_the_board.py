"""A project that has learned a lot about itself keeps its board.

The project region reached the prompt as one block that could not be trimmed, bidding for a
4,800-character budget, while `.kith/memory.md` inside it is uncapped on purpose. So the moment a
project's memory outgrew the budget, the whole region was dropped: board, folder standing and
memory together. Measured on 2026-09-27, six of nine active projects had lost it, and the three
that kept it were the three whose memory file was still the empty scaffold.

Held here: the board survives a memory file of any size, the memory file takes what room is left
and says how long it really is, and the ledger counts what was actually sent.
"""

from __future__ import annotations

import pytest

from kith.infra.db import repositories as repo
from kith.llm import ledger
from kith.services import conversations
from kith.services.turn import prompt as turn_prompt


@pytest.fixture
def a_project_that_knows_a_lot(db, monkeypatch, tmp_path):
    monkeypatch.setattr(turn_prompt, "AGENT_DB_PATH", db)
    folder = tmp_path / "vigil"
    (folder / ".kith").mkdir(parents=True)
    # 112k characters, the size of the real one that was being evicted.
    lines = ["# Vigil", "", "## Working here", "- Run the tests with `make check`."]
    lines += [f"- Finding {n}: the radar feed drops a frame when the queue is full." for n in range(1_700)]
    (folder / ".kith" / "memory.md").write_text("\n".join(lines))
    project = repo.projects.add_project(db, "Vigil", "A 24/7 watcher", str(folder))
    repo.projects.update_project(db, int(project["id"]), status="active")
    repo.tasks.add_task(db, goal="Wire the alert pipeline", project_id=int(project["id"]), status="working")
    chat = conversations.start(db, "a chat")
    repo.conversations.set_project(db, chat["id"], int(project["id"]))
    return db, chat["id"]


class TestTheBoardSurvives:
    def test_the_board_is_in_the_prompt(self, a_project_that_knows_a_lot):
        _, chat = a_project_that_knows_a_lot
        state = turn_prompt._present_state(chat, about="hey")
        assert "# The project you are in — " in state
        assert "Wire the alert pipeline" in state

    def test_the_memory_file_gets_what_is_left_and_the_start_of_it(self, a_project_that_knows_a_lot):
        _, chat = a_project_that_knows_a_lot
        state = turn_prompt._present_state(chat, about="hey")
        assert "Run the tests with `make check`" in state, "the start of the file is what a cold read needs"
        assert "Finding 1699" not in state
        # The budget counts blocks, not the blank lines that join them.
        assert len(state) <= turn_prompt.PRESENT_BUDGET + 20

    def test_it_says_how_long_the_file_really_is(self, a_project_that_knows_a_lot):
        _, chat = a_project_that_knows_a_lot
        state = turn_prompt._present_state(chat, about="hey")
        assert "characters — if this section ends before the file does" in state


class TestTheMeterCountsWhatWasSent:
    def test_the_project_line_is_not_zero(self, a_project_that_knows_a_lot):
        """It used to read 0 exactly when the region had been dropped, so the one number the
        uncapped memory file relied on to be noticed said nothing was there."""
        _, chat = a_project_that_knows_a_lot
        built = turn_prompt._build_messages([{"role": "user", "content": "hey"}], _config(), chat)
        book = ledger.take(built, window=1_000_000, chars_per_token=1.0)
        live = next(m for m in built if m.get("_live"))
        assert 0 < book.of("project") <= len(live["content"])
        assert live["_project_text"] and live["_project_text"] in live["content"]


def _config():
    from kith.config import default_config

    return default_config()
