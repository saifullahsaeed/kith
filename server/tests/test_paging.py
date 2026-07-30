"""Pages, and the ways a page can lie.

The failure this guards against is not an error — it is a truncated list that reads as
complete. He sees four tasks, concludes there are four, and acts on that. So the tests
about `total` and `more` matter as much as the ones about size.
"""

from __future__ import annotations

from kith.tools import paging


def rows(count: int, size: int = 20) -> list[dict]:
    return [{"id": i, "text": "x" * size} for i in range(count)]


class TestTheDefaultPage:
    def test_a_short_list_comes_back_whole(self):
        out = paging.page(rows(3), {})
        assert len(out["items"]) == 3
        assert out["total"] == 3
        assert "more" not in out

    def test_a_long_list_is_cut_to_the_default(self):
        out = paging.page(rows(200), {})
        assert len(out["items"]) == paging.DEFAULT_LIMIT

    def test_what_was_left_out_is_stated(self):
        out = paging.page(rows(200), {})
        assert out["total"] == 200
        assert out["more"] == 200 - paging.DEFAULT_LIMIT
        assert out["next_offset"] == paging.DEFAULT_LIMIT
        # The count alone gets skimmed; the sentence is what stops him concluding.
        assert "before concluding" in out["note"]

    def test_a_per_tool_default_is_honoured(self):
        assert len(paging.page(rows(50), {}, default=5)["items"]) == 5


class TestPaging:
    def test_offset_moves_the_window(self):
        out = paging.page(rows(50), {"offset": 10, "limit": 5})
        assert [item["id"] for item in out["items"]] == [10, 11, 12, 13, 14]
        assert out["showing"] == "11-15 of 50"

    def test_next_offset_walks_the_whole_list_exactly_once(self):
        seen, offset, guard = [], 0, 0
        while True:
            guard += 1
            assert guard < 50, "paging did not terminate"
            out = paging.page(rows(47), {"offset": offset, "limit": 5})
            seen += [item["id"] for item in out["items"]]
            if "next_offset" not in out:
                break
            offset = out["next_offset"]
        assert seen == list(range(47))

    def test_an_offset_past_the_end_is_empty_not_an_error(self):
        out = paging.page(rows(5), {"offset": 99})
        assert out["items"] == []
        assert out["total"] == 5

    def test_a_limit_over_the_cap_is_clamped(self):
        assert len(paging.page(rows(500), {"limit": 10_000})["items"]) <= paging.MAX_LIMIT

    def test_nonsense_arguments_fall_back_rather_than_raise(self):
        # Models send numbers as strings, and sometimes send prose.
        assert len(paging.page(rows(50), {"limit": "5"})["items"]) == 5
        assert len(paging.page(rows(50), {"limit": "lots"})["items"]) == paging.DEFAULT_LIMIT
        assert len(paging.page(rows(50), {"offset": -3, "limit": 2})["items"]) == 2


class TestTheCharacterBudget:
    """Item count is the wrong unit: twenty reminders and twenty journal entries are not
    the same payload, and it is the payload that costs."""

    def test_long_rows_are_cut_before_the_item_limit(self):
        out = paging.page(rows(20, size=2_000), {"limit": 20})
        assert len(out["items"]) < 20
        assert "long" in out["note"]
        assert out["next_offset"] == len(out["items"])

    def test_the_page_stays_near_the_budget(self):
        import json

        out = paging.page(rows(100, size=1_000), {"limit": 100})
        assert len(json.dumps(out["items"])) < paging.CHAR_BUDGET * 1.5

    def test_one_enormous_row_is_still_returned(self):
        # An empty page would read as "there is nothing here", which is a worse lie.
        out = paging.page(rows(3, size=paging.CHAR_BUDGET * 3), {})
        assert len(out["items"]) == 1

    def test_short_rows_are_not_cut_at_all(self):
        out = paging.page(rows(20, size=5), {"limit": 20})
        assert len(out["items"]) == 20
        assert "note" not in out


class TestFiltering:
    def test_contains_narrows_the_rows(self):
        pool = [{"id": 1, "text": "buy milk"}, {"id": 2, "text": "call Sam"}]
        assert paging.page(pool, {"contains": "sam"})["total"] == 1

    def test_the_filter_is_case_insensitive_and_searches_every_field(self):
        pool = [{"id": 1, "status": "DOING", "text": "x"}]
        assert paging.page(pool, {"contains": "doing"})["total"] == 1

    def test_total_counts_what_matched_not_what_exists(self):
        """Otherwise "1-3 of 200" would promise 197 more rows that no page can reach."""
        pool = rows(200) + [{"id": 999, "text": "needle"}]
        out = paging.page(pool, {"contains": "needle"})
        assert out["total"] == 1
        assert "more" not in out

    def test_a_filter_matching_nothing_says_so(self):
        out = paging.page(rows(50), {"contains": "nothing here"})
        assert out["items"] == []
        assert out["showing"] == "none of 0"
