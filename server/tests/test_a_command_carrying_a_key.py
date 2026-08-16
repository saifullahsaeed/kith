"""Noticing when a shell command has a credential typed into it.

From one real conversation: working against a live Odoo instance he ran 167 shell commands,
and 136 of them had the API key in the command text — because each was a fresh
`python3 - <<'PY'` heredoc that re-declared the connection. All 136 are in a transcript on
disk. A key that should have been written once is recorded 136 times.

Two things this had to get right, and both were learned by running it over the real corpus of
219 transcripts rather than by reasoning about it:

* **It matches shape, not name.** The first version looked for `api_key=` / `token=` / `secret=`
  and found 4 of the 136, because he had written `k='9a98…'`. A one-letter variable holding a
  forty-character key is still a key.
* **It does not fire on identifiers.** The version after that accepted any long quoted literal
  and flagged `l10n_sa_edi_…` and `payment_…` — Odoo module names. Requiring an unbroken run of
  letters and digits fixed it: identifiers have separators, generated keys do not.

Final specificity on the real corpus: 2 transcripts of 219, both genuinely carrying the same
key, nothing else.
"""

from __future__ import annotations

import pytest

from kith.domain import secrets

#: Shaped like the one this was written for — forty hex characters, no separators — but not a
#: real credential.
LIKE_THE_REAL_ONE = "9a3f8b2c7e1d4a6f0b9c8d7e5a4f3b2c1d0e9f8a"


class TestWhatItCatches:
    def test_a_key_in_a_one_letter_variable(self):
        """The exact shape that got past the first attempt."""
        assert secrets.carries_a_secret(f"python3 -c \"k='{LIKE_THE_REAL_ONE}'; go(k)\"")

    def test_a_key_passed_as_a_bare_argument(self):
        assert secrets.carries_a_secret(f"authenticate('db', 'admin@x.com', '{LIKE_THE_REAL_ONE}')")

    @pytest.mark.parametrize(
        "token",
        [
            "sk-abcdefghijklmnopqrstuvwxyz012345",
            "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
            "xoxb-1234567890-abcdefghijkl",
            "AKIAIOSFODNN7EXAMPLE",
        ],
    )
    def test_formats_that_announce_themselves(self, token):
        """Matched directly. Several are shorter or less random than the general rule would
        take on its own, and there is no ambiguity about what they are."""
        assert secrets.carries_a_secret(f"curl -H 'Authorization: {token}' https://x")


class TestWhatItLeavesAlone:
    @pytest.mark.parametrize(
        "literal",
        [
            "l10n_sa_edi_extended_fields",  # the real false positive, an Odoo module
            "payment_transaction_reference",
            "background_task_runner_state",
            "integration_test_configuration_helper",
        ],
    )
    def test_a_long_identifier_is_not_a_key(self, literal):
        """These were being flagged. An identifier has separators; a generated key does not."""
        assert not secrets.carries_a_secret(f"env.search([('name','=','{literal}')])")

    def test_a_sentence_is_not_a_key(self):
        assert not secrets.carries_a_secret("echo 'this is a perfectly ordinary sentence here'")

    def test_a_long_number_is_not_a_key(self):
        assert not secrets.carries_a_secret("echo '12345678901234567890123456789012345'")

    def test_a_long_word_is_not_a_key(self):
        assert not secrets.carries_a_secret("echo 'abcdefghijklmnopqrstuvwxyzabcdefgh'")

    def test_an_ordinary_command_says_nothing(self):
        assert not secrets.carries_a_secret("git status --short")

    def test_a_short_hex_string_is_not_a_key(self):
        """A commit is not a credential."""
        assert not secrets.carries_a_secret("git show '4d87fc9'")


class TestThroughTheTool:
    @pytest.fixture(autouse=True)
    def workspace_root(self, tmp_path, monkeypatch):
        from kith.infra import workspace as ws

        monkeypatch.setattr(ws.paths.settings, "WORKSPACE_DIR", str(tmp_path), raising=False)
        return tmp_path

    def test_the_note_arrives_beside_the_output(self, monkeypatch):
        from kith.tools import computer

        class Ran:
            exit_code = 0
            output = "done"

        monkeypatch.setattr(computer.sandbox, "run_command", lambda _c: Ran())
        out = computer.shell(None, {"command": f"python3 -c \"k='{LIKE_THE_REAL_ONE}'\""})
        assert out["output"] == "done", "the command still ran and still reported"
        assert ".kith/scratch/" in out["note"], "and the note names the remedy"

    def test_an_ordinary_command_gets_no_note(self, monkeypatch):
        from kith.tools import computer

        class Ran:
            exit_code = 0
            output = "ok"

        monkeypatch.setattr(computer.sandbox, "run_command", lambda _c: Ran())
        assert "note" not in computer.shell(None, {"command": "ls -la"})

    def test_the_note_never_repeats_the_secret(self, monkeypatch):
        """It is a remark about the record. Putting the key in it would be the same mistake."""
        from kith.tools import computer

        class Ran:
            exit_code = 0
            output = "done"

        monkeypatch.setattr(computer.sandbox, "run_command", lambda _c: Ran())
        out = computer.shell(None, {"command": f"go('{LIKE_THE_REAL_ONE}')"})
        assert LIKE_THE_REAL_ONE not in out["note"]

    def test_a_failing_command_still_reports_its_failure(self, monkeypatch):
        from kith.tools import computer

        class Ran:
            exit_code = 2
            output = "boom"

        monkeypatch.setattr(computer.sandbox, "run_command", lambda _c: Ran())
        out = computer.shell(None, {"command": f"go('{LIKE_THE_REAL_ONE}')"})
        assert out["exitCode"] == 2 and out["output"] == "boom"
