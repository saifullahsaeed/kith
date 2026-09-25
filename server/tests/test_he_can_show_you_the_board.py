"""He can put a design in front of you, instead of describing one.

Asked to show a board he went looking for the word: `find -iname "*board*"`, `git log | grep
board`, two plugin reads, `ls boards/`. Three commands to not find a thing that was already one
click away on the screen in front of his person — because the surface that draws designs belongs
to the interface, and nothing he had could open it.

The tool returns what is actually on the board rather than an acknowledgement, for the reason
`add_deliverable` returns its row: "ok" teaches him nothing about what happened, and the titles
are what his next sentence has to name.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kith.infra.db import migrations
from kith.infra.db import repositories as repo
from kith.services import showing
from kith.tools import tasks as task_tools


@pytest.fixture
def brain(tmp_path: Path) -> Path:
    place = tmp_path / "agent.db"
    migrations.init(place)
    return place


@pytest.fixture(autouse=True)
def nothing_held_over():
    showing.clear()
    yield
    showing.clear()


def _task_with(brain: Path, *deliverables: tuple[str, str, str]) -> int:
    task = repo.tasks.add_task(brain, "Engagements table")
    tid = task["id"] if isinstance(task, dict) else task
    for kind, title, content in deliverables:
        repo.tasks.add_deliverable(brain, tid, kind, title, content)
    return tid


class TestShowing:
    def test_it_puts_the_board_in_front_of_them(self, brain):
        tid = _task_with(
            brain,
            ("file", "A — comfortable", "screens/a.html"),
            ("file", "B — compact", "screens/b.html"),
        )

        answer = task_tools.show_canvas(brain, {"id": tid})
        assert answer["shown"] is True

        held = showing.current()
        assert held["kind"] == "board"
        assert held["taskId"] == tid

    def test_it_answers_with_what_is_on_it(self, brain):
        """Not "ok". The titles are what his next sentence has to name."""
        tid = _task_with(
            brain,
            ("file", "A — comfortable", "screens/a.html"),
            ("file", "B — compact", "screens/b.html"),
        )
        answer = task_tools.show_canvas(brain, {"id": tid})
        assert [one["title"] for one in answer["designs"]] == ["A — comfortable", "B — compact"]
        assert answer["task"] == "Engagements table"

    def test_a_note_rides_along(self, brain):
        tid = _task_with(brain, ("file", "A", "screens/a.html"))
        task_tools.show_canvas(brain, {"id": tid, "note": "look at the row height"})
        assert showing.current()["note"] == "look at the row height"


class TestWhenThereIsNothingToShow:
    def test_a_task_with_no_designs_is_told_so_rather_than_opened_empty(self, brain):
        """An empty board looks broken, and would be his fault in a way he could not see."""
        tid = _task_with(brain, ("text", "A written result", "some prose"))

        answer = task_tools.show_canvas(brain, {"id": tid})
        assert answer["shown"] is False
        assert "no design files" in answer["why"]
        assert showing.current() is None

    def test_a_task_that_does_not_exist(self, brain):
        answer = task_tools.show_canvas(brain, {"id": 9999})
        assert answer["shown"] is False
        assert showing.current() is None

    def test_a_deliverable_that_is_a_file_but_not_a_design(self, brain):
        tid = _task_with(brain, ("file", "The report", "reports/q4.pdf"))
        assert task_tools.show_canvas(brain, {"id": tid})["shown"] is False


class TestTaking:
    def test_it_is_forgotten_once_taken(self, brain):
        """It means "he just asked", not "this is on screen" — otherwise relaunching the app
        reopens a board from last Tuesday."""
        tid = _task_with(brain, ("file", "A", "screens/a.html"))
        task_tools.show_canvas(brain, {"id": tid})
        assert showing.current() is not None

        showing.clear()
        assert showing.current() is None

    def test_the_latest_one_wins(self, brain):
        first = _task_with(brain, ("file", "A", "screens/a.html"))
        second = _task_with(brain, ("file", "B", "screens/b.html"))
        task_tools.show_canvas(brain, {"id": first})
        task_tools.show_canvas(brain, {"id": second})
        assert showing.current()["taskId"] == second


class TestTheOldName:
    def test_show_board_still_reaches_the_canvas(self):
        """Renamed to `show_canvas` when the ```html fence was retired. Old transcripts and his
        habits still say `show_board`, and a retired name runs the new tool rather than failing."""
        from kith.tools import aliases

        assert aliases.retired("show_board").now == "show_canvas"
