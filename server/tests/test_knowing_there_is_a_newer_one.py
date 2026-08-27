"""A downloaded Kith has no way of finding out that it is old.

The releases are on GitHub and the app never looked, so someone who installed 0.3.0 in August is
still on it — not because they decided to be, but because nothing ever said otherwise.

Told, not installed: macOS auto-update goes through Squirrel, which verifies the downloaded app's
signature against the running one, and Kith is ad-hoc signed. That check cannot pass without a
paid Developer ID, so the honest thing is to say a new version exists and hand over the download.
"""

from __future__ import annotations

import pytest
import requests

from kith.services import updates


@pytest.fixture(autouse=True)
def a_cold_cache(monkeypatch):
    """Nothing here may reach the network, and no test may inherit another's answer."""
    monkeypatch.setattr(updates, "_cached", None, raising=False)
    monkeypatch.setattr(updates, "_checked_at", 0.0, raising=False)
    monkeypatch.setattr(
        updates,
        "_fetch",
        lambda: pytest.fail("a test reached for the network"),
        raising=False,
    )


def release(tag: str = "v0.6.0", asset: str = "Kith-0.6.0-arm64.dmg") -> dict:
    return {
        "tag_name": tag,
        "html_url": f"https://github.com/saifullahsaeed/kith/releases/tag/{tag}",
        "body": "what changed",
        "published_at": "2026-08-22T16:06:05Z",
        "assets": [
            {"name": "Kith.dmg.blockmap", "browser_download_url": "https://example/blockmap"},
            {"name": asset, "browser_download_url": f"https://example/{asset}"},
        ],
    }


class TestWhichIsNewer:
    @pytest.mark.parametrize(
        ("latest", "current", "newer"),
        [
            ("0.6.0", "0.5.0", True),
            ("0.5.0", "0.5.0", False),
            ("0.4.0", "0.5.0", False),
            ("v0.6.0", "0.5.0", True),  # the tag carries a v; the version does not
            ("0.10.0", "0.9.0", True),  # numbers as numbers — a string compare says 0.10 < 0.9
            ("1.0.0", "0.99.9", True),
            ("0.5.1", "0.5.0", True),
            ("0.5", "0.5.0", False),  # padded, so a short version is not treated as older
            ("", "0.5.0", False),
            ("0.6.0", "", False),  # a checkout has nothing to compare against
        ],
    )
    def test_the_comparison(self, latest, current, newer):
        assert updates.is_newer(latest, current) is newer

    def test_a_prerelease_is_older_than_the_release_it_leads_to(self):
        """This repo has shipped `v0.2.0-beta`, and a string compare gets it backwards."""
        assert updates.is_newer("0.2.0", "0.2.0-beta") is True
        assert updates.is_newer("0.2.0-beta", "0.2.0") is False


class TestRunningFromACheckout:
    def test_it_knows_the_version_but_offers_no_download(self, monkeypatch):
        """Two separate questions, and they were one.

        `packaged` decides whether a dmg is offered — handing one to someone running from source
        would answer a question they did not ask. Knowing *which* version is a different matter,
        and a checkout does know: `desktop/package.json` is the file the release workflow tags.
        Reporting no version at all left the settings footer saying only "running from source",
        which is the wrong half — the number is the first thing anyone is asked for in a bug
        report.
        """
        monkeypatch.delenv("KITH_APP_VERSION", raising=False)
        state = updates.check()
        assert state["packaged"] is False, "no dmg for a working tree"
        assert state["current"], "but it still knows which version it is"
        assert state["newer"] is False
        assert state["error"] == ""

    def test_the_version_it_reports_is_the_one_the_release_workflow_tags(self, monkeypatch):
        """Read from the same file CI reads, so the two cannot disagree about what is running."""
        import json

        from kith import settings

        monkeypatch.delenv("KITH_APP_VERSION", raising=False)
        manifest = settings.SERVER_ROOT.parent / "desktop" / "package.json"
        expected = json.loads(manifest.read_text(encoding="utf-8"))["version"]
        assert updates.current_version() == expected

    def test_the_shell_still_wins_when_it_says_so(self, monkeypatch):
        """An installed copy is told its version; the file beside it, if any, is not the truth."""
        monkeypatch.setenv("KITH_APP_VERSION", "v9.9.9")
        assert updates.current_version() == "9.9.9"  # the tag's `v` is not part of the version
        assert updates.is_packaged() is True


class TestAnInstalledCopy:
    @pytest.fixture(autouse=True)
    def installed(self, monkeypatch):
        monkeypatch.setenv("KITH_APP_VERSION", "0.5.0")

    def test_it_finds_the_dmg_and_not_the_blockmap(self, monkeypatch):
        monkeypatch.setattr(updates, "_fetch", lambda: release())
        state = updates.check()
        assert state["newer"] is True
        assert state["latest"] == "0.6.0"
        assert state["download"].endswith("Kith-0.6.0-arm64.dmg")
        assert state["notes"] == "what changed"

    def test_it_falls_back_to_the_release_page_when_there_is_no_dmg(self, monkeypatch):
        monkeypatch.setattr(updates, "_fetch", lambda: release(asset="notes.txt"))
        state = updates.check()
        assert state["download"] == ""
        assert state["page"].endswith("/v0.6.0")  # never an empty button

    def test_being_current_is_not_an_update(self, monkeypatch):
        monkeypatch.setattr(updates, "_fetch", lambda: release(tag="v0.5.0"))
        assert updates.check()["newer"] is False

    def test_it_asks_once_and_then_remembers(self, monkeypatch):
        calls = []

        def counted():
            calls.append(1)
            return release()

        monkeypatch.setattr(updates, "_fetch", counted)
        updates.check()
        updates.check()
        updates.check()
        assert len(calls) == 1, "six hours between checks, not one per caller"
        updates.check(force=True)
        assert len(calls) == 2, "'Check now' has to actually look"


class TestWhenGitHubCannotBeReached:
    @pytest.fixture(autouse=True)
    def installed(self, monkeypatch):
        monkeypatch.setenv("KITH_APP_VERSION", "0.5.0")

    def raises(self):
        raise requests.ConnectionError("no route to host")

    def test_it_says_so_rather_than_claiming_you_are_current(self, monkeypatch):
        """The one failure that must not look like success.

        A screen that cannot tell "could not reach GitHub" from "you are up to date" will
        eventually tell someone they are current when nobody has looked in a month.
        """
        monkeypatch.setattr(updates, "_fetch", self.raises)
        state = updates.check()
        assert state["error"]
        assert state["latest"] == ""
        assert state["newer"] is False

    def test_it_keeps_the_last_good_answer(self, monkeypatch):
        """An update found yesterday does not stop existing because the wifi dropped."""
        monkeypatch.setattr(updates, "_fetch", lambda: release())
        assert updates.check()["newer"] is True

        monkeypatch.setattr(updates, "_fetch", self.raises)
        state = updates.check(force=True)
        assert state["newer"] is True, "the update is still there"
        assert state["latest"] == "0.6.0"
        assert state["error"], "and it still says the look failed"

    def test_a_body_that_is_not_json_is_a_failure_not_a_crash(self, monkeypatch):
        def nonsense():
            raise ValueError("Expecting value")

        monkeypatch.setattr(updates, "_fetch", nonsense)
        assert updates.check()["error"]
