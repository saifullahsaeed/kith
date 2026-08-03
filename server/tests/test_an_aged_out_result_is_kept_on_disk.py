"""An aged-out tool result is spilled to a file, not thrown away.

The compactor evicts old tool results to stay in budget. Truncating them loses the tail,
so the model re-runs the tool and pays for the same output twice. Instead the full result
is written where `read_file` can reach it and the stub points at the path — the field's
"offload to disk, keep a path" pattern (Anthropic context-editing, Cursor file-backed
outputs).
"""

from __future__ import annotations

from pathlib import Path

from kith.infra import workspace
from kith.services import agent_loop, offload, tuning


class TestSpillingToDisk:
    def test_a_saved_result_reads_back_whole(self, tmp_path, monkeypatch):
        monkeypatch.setattr(workspace.settings, "WORKSPACE_DIR", str(tmp_path))
        body = "R" * 5000

        path = offload.save("conv1", "web_search", body)

        assert Path(path).read_text() == body

    def test_the_file_lives_inside_the_workspace_so_a_read_is_never_gated(self, tmp_path, monkeypatch):
        # Outside the root every read prompts; the whole point is that this one never does.
        monkeypatch.setattr(workspace.settings, "WORKSPACE_DIR", str(tmp_path))

        path = Path(offload.save("conv1", "shell", "x" * 3000))

        assert str(path).startswith(str(tmp_path))

    def test_clearing_a_conversation_removes_its_spill(self, tmp_path, monkeypatch):
        monkeypatch.setattr(workspace.settings, "WORKSPACE_DIR", str(tmp_path))
        path = Path(offload.save("conv1", "shell", "x" * 3000))
        assert path.exists()

        offload.clear("conv1")

        assert not path.exists()


class TestTheStubPointsAtTheFile:
    def _convo(self):
        # Bodies over the 4,000-char floor for `live_tool_chars`, so the oldest is genuinely
        # pushed out of the live window rather than merely eligible.
        big = "R" * 2500
        return [
            {"role": "system", "content": "persona"},
            {"role": "tool", "tool_name": "web_search", "content": big},  # oldest — evicted
            {"role": "tool", "tool_name": "read_file", "content": big},  # newest — kept whole
        ]

    def test_an_evicted_result_names_its_offloaded_path(self):
        tuning.apply({"live_tool_chars": 4000, "keep_full_tool_results": 1, "tool_stub_chars": 100})
        convo = self._convo()
        saved: list = []

        def fake_offload(name, content):
            saved.append((name, content))
            return f"/ws/.kith/offload/{name}.txt"

        agent_loop._compact_tool_history(convo, fake_offload)

        assert saved == [("web_search", "R" * 2500)]
        assert "/ws/.kith/offload/web_search.txt" in convo[1]["content"]
        assert "read_file" in convo[1]["content"].lower()

    def test_without_an_offloader_it_still_just_trims(self):
        # Backward-compatible: the old truncate-in-place path is unchanged when nobody offloads.
        tuning.apply({"live_tool_chars": 4000, "keep_full_tool_results": 1, "tool_stub_chars": 100})
        convo = self._convo()

        agent_loop._compact_tool_history(convo)

        assert convo[1]["content"].startswith("R" * 100)
        assert "trimmed to save room" in convo[1]["content"]
