"""The name a task keeps when it leaves. See `kith/domain/keys.py`.

Sortable because these end up in filenames, where sorting by name is how a person finds the
recent one; hex because macOS will not distinguish `a1b` from `A1B` and would merge two tasks
into one file.
"""

from __future__ import annotations

from kith.domain import keys


class TestItIsUniqueWithoutAskingAnybody:
    def test_a_thousand_in_a_row_are_all_different(self):
        assert len({keys.new_key() for _ in range(1000)}) == 1000

    def test_two_in_the_same_millisecond_still_differ(self):
        """The case that matters: two machines, no coordination, same instant."""
        at = 1_760_000_000_000
        assert len({keys.new_key(at) for _ in range(200)}) > 190


class TestItSortsByWhenItWasMade:
    def test_later_is_larger(self):
        assert keys.new_key(1_000) < keys.new_key(2_000)

    def test_which_is_what_makes_a_directory_listing_useful(self):
        made = [keys.new_key(t) for t in (5_000, 1_000, 3_000)]
        assert sorted(made) == [made[1], made[2], made[0]]


class TestItIsSafeInAFilename:
    def test_only_hex(self):
        assert all(c in "0123456789abcdef" for c in keys.new_key())

    def test_case_cannot_make_two_names_out_of_one(self):
        """macOS is case-insensitive. Base32 would let `A1B` and `a1b` be different keys and the
        same file."""
        key = keys.new_key()
        assert key == key.lower()

    def test_the_length_is_fixed(self):
        assert all(len(keys.new_key(t)) == keys.KEY_LENGTH for t in (0, 1, 1_760_000_000_000))


class TestTellingItFromAnId:
    def test_a_key_is_recognised(self):
        assert keys.looks_like_a_key(keys.new_key())

    def test_a_task_number_is_not(self):
        for number in ("7", "07", "102", "99999"):
            assert not keys.looks_like_a_key(number), number

    def test_neither_is_a_slug_or_a_blank(self):
        for text in ("", "redesign", "task-102", "  ", "z" * 16):
            assert not keys.looks_like_a_key(text), text

    def test_it_is_read_case_insensitively_even_though_it_is_written_lower(self):
        assert keys.looks_like_a_key(keys.new_key().upper())
