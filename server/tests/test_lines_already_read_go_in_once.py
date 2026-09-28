"""A read of lines the turn already holds, unchanged, puts in a note instead of the lines.

Measured Sep 13-27: 533 of 1,982 `read_file` path-reads repeated a path already read that turn,
and one was byte-identical — the only kind the identical-copy dedupe
(`test_reading_the_same_file_twice`) can see. 60% of reads ask for a window, so where a repeat
duplicates anything it is an overlapping window, and the unit that repeats is the *line*. Most
repeats duplicate nothing at all — a new slice of the file — and `coverage`'s docstring has the
breakdown; the test for that case is here so nobody "fixes" it into leaving something out.

These drive the real `read_file` against a real file, because the whole mechanism rests on the
tool's output format — six-column line numbers and a tab — and a hand-written payload would keep
passing after that format changed and this quietly stopped matching anything.

Three properties matter more than the saving, and most of the tests are about them:

* **It never hides a change.** Comparison is by line text, so an edited line goes in again, and
  an edit that moves line numbers moves everything below it back in too.
* **It never points at what is gone.** A pointer's target can be stubbed, folded or dropped later
  in the turn; `_settle_pointers` puts the whole result back when it is.
* **Nothing already sent is touched** on insert. The history stays append-only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kith.domain.chat import Config
from kith.llm import budget, ledger
from kith.llm.budget import ContextBudget
from kith.services import agent_loop, compaction, tuning
from kith.services.turn import history
from kith.tools import computer


@pytest.fixture
def root(tmp_path, monkeypatch):
    from kith.infra import workspace

    monkeypatch.setattr(workspace.paths, "configured_root", lambda: tmp_path)
    return tmp_path


def _write(root: Path, name: str, lines: int = 300) -> Path:
    """Distinct, realistic lines — every one different, so a moved number cannot match by luck."""
    path = root / name
    path.write_text(
        "\n".join(f"def handler_{i}(request):  # route {i} of {name}" for i in range(1, lines + 1))
    )
    return path


def _read(convo: list[dict], **arguments) -> dict:
    """One `read_file` call and its result, appended the way the loop appends them."""
    convo.append(
        {"role": "assistant", "tool_calls": [{"function": {"name": "read_file", "arguments": arguments}}]}
    )
    result = computer.read_file(Path(), arguments)
    message = history._tool_result_message(convo, "read_file", json.dumps({"ok": True, "result": result}))
    convo.append(message)
    return message


def _text(message: dict) -> str:
    return json.loads(message["content"])["result"]


def _numbered(message: dict) -> list[int]:
    return [int(line.split("\t")[0]) for line in _text(message).split("\n") if line.strip()[:1].isdigit()]


def _start() -> list[dict]:
    return [{"role": "system", "content": "persona"}, {"role": "user", "content": "go"}]


class TestWhatAlreadyHeldIsLeftOut:
    def test_a_window_inside_an_earlier_one_goes_in_as_a_note(self, root):
        _write(root, "app.py")
        convo = _start()
        _read(convo, paths=["app.py"], offset=1, limit=120)
        again = _read(convo, paths=["app.py"], offset=20, limit=60)

        assert _numbered(again) == [], "every line was already above and still went in"
        assert "lines 20-79 unchanged" in _text(again)
        assert "lines 1-120" in _text(again), "the note should say which read holds them"
        assert "still above, in full" in _text(again)

    def test_an_overlapping_window_brings_only_the_new_lines(self, root):
        _write(root, "app.py")
        convo = _start()
        _read(convo, paths=["app.py"], offset=1, limit=100)
        later = _read(convo, paths=["app.py"], offset=50, limit=100)

        assert _numbered(later) == list(range(101, 150))
        assert "lines 50-100 unchanged" in _text(later)
        # The tool's own trailer survives: he still needs to know there is more of the file.
        assert "read with offset=150" in _text(later)

    def test_a_window_that_overlaps_nothing_goes_in_whole(self, root):
        """Most repeats measured were exactly this — a new slice of a file already opened — and
        there is nothing in it to leave out."""
        _write(root, "app.py")
        convo = _start()
        _read(convo, paths=["app.py"], offset=1, limit=50)
        payload = json.dumps(
            {"ok": True, "result": computer.read_file(Path(), {"paths": ["app.py"], "offset": 200})}
        )
        fresh = _read(convo, paths=["app.py"], offset=200)
        assert fresh["content"] == payload
        assert "_whole" not in fresh

    def test_one_file_of_a_batch_is_left_out_and_the_other_is_not(self, root):
        _write(root, "a.py")
        _write(root, "b.py")
        convo = _start()
        _read(convo, paths=["a.py"], offset=1, limit=80)
        batch = _text(_read(convo, paths=["a.py", "b.py"], offset=1, limit=80))

        a, b = batch.split("===== b.py =====")
        assert "lines 1-80 unchanged" in a
        assert "handler_40(" not in a
        assert "handler_40(" in b, "b.py was never read and must arrive whole"

    def test_a_path_spelled_another_way_is_the_same_file(self, root):
        _write(root, "app.py")
        convo = _start()
        _read(convo, paths=["app.py"], offset=1, limit=100)
        assert _numbered(_read(convo, paths=["./app.py"], offset=10, limit=40)) == []


class TestItNeverHidesAChange:
    def test_an_edited_line_goes_in_again_and_the_rest_does_not(self, root):
        path = _write(root, "app.py")
        convo = _start()
        _read(convo, paths=["app.py"], offset=1, limit=100)
        lines = path.read_text().split("\n")
        lines[59] = "def handler_60(request):  # REWRITTEN"
        path.write_text("\n".join(lines))

        again = _read(convo, paths=["app.py"], offset=1, limit=100)

        assert _numbered(again) == [60]
        assert "REWRITTEN" in _text(again)
        assert "lines 1-59 unchanged" in _text(again)
        assert "lines 61-100 unchanged" in _text(again)

    def test_a_line_added_above_moves_everything_back_in(self, root):
        """Line 40 is now what line 39 was. Same text, different number — so not the same fact."""
        path = _write(root, "app.py")
        convo = _start()
        _read(convo, paths=["app.py"], offset=1, limit=100)
        path.write_text("import os\n" + path.read_text())

        again = _read(convo, paths=["app.py"], offset=1, limit=100)
        assert _numbered(again) == list(range(1, 101))
        assert "_whole" not in again

    def test_a_line_whose_earlier_copies_disagree_is_never_left_out(self, root):
        """Two versions of line 50 are above. "As you read it earlier" would not say which, so it
        goes in — and the lines every copy agrees on still do not."""
        path = _write(root, "app.py")
        convo = _start()
        _read(convo, paths=["app.py"], offset=1, limit=100)
        lines = path.read_text().split("\n")
        lines[49] = "def handler_50(request):  # v2"
        path.write_text("\n".join(lines))
        _read(convo, paths=["app.py"], offset=45, limit=10)  # holds the new line 50 whole

        third = _read(convo, paths=["app.py"], offset=1, limit=100)
        assert 50 in _numbered(third)
        assert "lines 1-44 unchanged" in _text(third)

    def test_a_short_run_is_not_worth_a_note(self, root):
        _write(root, "app.py")
        convo = _start()
        _read(convo, paths=["app.py"], offset=1, limit=50)
        # Lines 48-50 overlap: three short lines, well under what a note is worth.
        later = _read(convo, paths=["app.py"], offset=48, limit=30)
        assert _numbered(later) == list(range(48, 78))
        assert "_whole" not in later

    def test_only_read_file_results_are_taken_as_lines_he_has(self, root):
        """A grep hit looks line-numbered too, and is a different fact about the codebase."""
        _write(root, "app.py")
        convo = _start()
        body = computer.read_file(Path(), {"paths": ["app.py"], "limit": 100})
        convo.append({"role": "assistant", "tool_calls": [{"function": {"name": "grep", "arguments": {}}}]})
        convo.append(
            {"role": "tool", "tool_name": "grep", "content": json.dumps({"ok": True, "result": body})}
        )
        assert "_whole" not in _read(convo, paths=["app.py"], limit=100)


class TestNothingSentIsTouched:
    def test_appending_a_pointer_changes_nothing_above_it(self, root):
        _write(root, "app.py")
        convo = _start()
        _read(convo, paths=["app.py"], offset=1, limit=120)
        before = [dict(m) for m in convo]
        _read(convo, paths=["app.py"], offset=10, limit=50)
        assert convo[: len(before)] == before

    def test_two_calls_in_one_round_are_each_matched_to_their_own_file(self, root):
        """Results pair to calls by position, the way `openai_compat._to_openai` pairs them."""
        _write(root, "a.py")
        _write(root, "b.py")
        convo = _start()
        _read(convo, paths=["a.py"], limit=100)
        asked = [
            {"paths": ["b.py"], "offset": 10, "limit": 50},
            {"paths": ["a.py"], "offset": 10, "limit": 50},
        ]
        convo.append(
            {
                "role": "assistant",
                "tool_calls": [{"function": {"name": "read_file", "arguments": a}} for a in asked],
            }
        )
        for arguments in asked:
            payload = json.dumps({"ok": True, "result": computer.read_file(Path(), arguments)})
            convo.append(history._tool_result_message(convo, "read_file", payload))
        assert "_whole" not in convo[-2], "b.py was never read and was left out anyway"
        assert _numbered(convo[-1]) == [], "a.py was held whole and went in again"


class TestAPointerIsNeverLeftPointingAtNothing:
    def _pointed(self, root) -> tuple[list[dict], dict, dict]:
        _write(root, "app.py")
        convo = _start()
        original = _read(convo, paths=["app.py"], offset=1, limit=120)
        pointer = _read(convo, paths=["app.py"], offset=20, limit=60)
        assert _numbered(pointer) == []
        return convo, original, pointer

    def test_settling_a_turn_where_nothing_moved_changes_nothing(self, root):
        convo, _, _ = self._pointed(root)
        before = [dict(m) for m in convo]
        assert history._settle_pointers(convo) is False
        assert convo == before

    def test_a_stubbed_original_brings_the_lines_back(self, root, monkeypatch):
        convo, original, pointer = self._pointed(root)
        # A budget so small only the newest result survives whole — the real stubber, not a
        # hand-set flag, because the flag is not the only thing that changes.
        settings = {"keep_full_tool_results": 1, "live_tool_chars": 4_000, "tool_stub_chars": 1_200}
        monkeypatch.setattr(tuning, "value", lambda key: settings[key])
        history._compact_tool_history(convo, window=0)
        assert original.get("_stubbed") and not pointer.get("_stubbed")

        assert history._settle_pointers(convo) is True
        assert pointer["content"] == json.dumps(
            {
                "ok": True,
                "result": computer.read_file(Path(), {"paths": ["app.py"], "offset": 20, "limit": 60}),
            }
        )
        assert "_whole" not in pointer and "_deduped" not in pointer

    def test_a_dropped_original_brings_the_lines_back(self, root):
        convo, _, pointer = self._pointed(root)
        _read(convo, paths=["app.py"], offset=250, limit=10)  # an exchange after, so a drop may happen
        assert history._drop_oldest_exchange(convo)
        assert history._settle_pointers(convo) is True
        assert _numbered(pointer) == list(range(20, 80))

    def test_a_folded_original_brings_the_lines_back(self, root):
        convo, _, pointer = self._pointed(root)
        # The fold keeps the last fourteen messages; pad after the pointer until the original
        # falls into the middle it summarises and the pointer does not.
        for offset in range(200, 290, 15):
            _read(convo, paths=["app.py"], offset=offset, limit=5)
        assert compaction.fold(convo, lambda prompt: "notes")
        assert any(m is pointer for m in convo), "the pointer should be in the tail the fold keeps"

        assert history._settle_pointers(convo) is True
        assert _numbered(pointer) == list(range(20, 80))

    def test_an_identical_copy_whose_original_went_comes_back_too(self, root, monkeypatch):
        """The same repair for the byte-identical pointer, which had the same hole."""
        _write(root, "app.py")
        convo = _start()
        original = _read(convo, paths=["app.py"], limit=120)
        copy = _read(convo, paths=["app.py"], limit=120)
        assert copy["content"].startswith("[identical to")

        settings = {"keep_full_tool_results": 1, "live_tool_chars": 4_000, "tool_stub_chars": 1_200}
        monkeypatch.setattr(tuning, "value", lambda key: settings[key])
        history._compact_tool_history(convo, window=0)
        assert original.get("_stubbed")

        assert history._settle_pointers(convo) is True
        assert _numbered(copy) == list(range(1, 121))

    def test_settling_is_idempotent(self, root):
        convo, _, _ = self._pointed(root)
        assert history._drop_oldest_exchange(convo)
        assert history._settle_pointers(convo) is True
        after = [dict(m) for m in convo]
        assert history._settle_pointers(convo) is False
        assert convo == after

    def test_the_loop_settles_before_it_sends(self, root, monkeypatch):
        """The repair is only worth anything if the loop runs it after every move that can take
        a result away and before the request is measured — the end of `_make_room`."""
        convo, original, pointer = self._pointed(root)
        real = tuning.value
        small = {"keep_full_tool_results": 1, "live_tool_chars": 4_000, "tool_stub_chars": 1_200}
        monkeypatch.setattr(tuning, "value", lambda key: small[key] if key in small else real(key))
        config = Config(model="m", num_ctx=0, num_predict=1_000, system="", think=False)
        room = ContextBudget()

        making = agent_loop._make_room(
            convo, [], room, take_reading=lambda: ledger.take(convo), config=config, host=""
        )
        for _ in making:
            pass

        assert original.get("_stubbed"), "the setup should have stubbed what the pointer points at"
        assert _numbered(pointer) == list(range(20, 80)), "the request went out pointing at a stub"


class TestTheMeterCountsWhatIsSent:
    def test_what_a_pointer_stands_in_for_is_not_counted(self, root):
        """`_whole` rides on the message for `_settle_pointers`, and never reaches a provider.
        Counted, the pointer would cost the meter exactly what it saved the request."""
        _write(root, "app.py")
        convo = _start()
        _read(convo, paths=["app.py"], limit=120)
        pointer = _read(convo, paths=["app.py"], offset=20, limit=60)

        sent = {key: value for key, value in pointer.items() if not key.startswith("_")}
        assert budget.message_chars(pointer) == budget.message_chars(sent)
        assert ledger.take([pointer], chars_per_token=1.0).of("code") < len(pointer["_whole"]) // 4
