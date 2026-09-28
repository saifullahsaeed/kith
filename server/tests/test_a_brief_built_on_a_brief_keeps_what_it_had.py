"""A brief built on a brief keeps what the first one said, word for word.

Measured between two consecutive folds of one real conversation: "What was done" went from 6,455
characters to 2,421 and "What was found" from 4,018 to 1,950, while "What they asked for" grew to
nineteen items. The fold was asked to carry its earlier brief forward, under an output cap that
had already bound on the fold before — so it compressed it, and every fold after compressed what
was left. The start of a long conversation decays to nothing that way.

So the four sections that only ever grow are carried by the code, and the model is asked only
for what is new under them. The two that describe the present are rewritten whole each time,
because the present is the part that changed.
"""

from __future__ import annotations

from kith.services import brief, history


def _brief(**sections: str) -> str:
    """A brief with the given bodies, keyed by the first word after "What"/"Where"."""
    keys = {"asked": 0, "decided": 1, "done": 2, "found": 3, "open": 4, "stands": 5}
    bodies = ["nothing"] * 6
    for key, body in sections.items():
        bodies[keys[key]] = body
    return "\n\n".join(f"## {heading}\n{body}" for heading, body in zip(brief.SECTIONS, bodies, strict=True))


def _section(text: str, heading: str) -> str:
    return brief._sections(text)[heading]


class TestTheLastingSectionsAreCarriedByTheCode:
    def test_what_was_done_before_is_kept_word_for_word_and_the_new_goes_beneath(self):
        before = _brief(done="- wrote `report.xlsx` with 14 tabs\n- fixed the VAT column")
        new = _brief(done="- sent it to Omar")

        kept = _section(brief.carried(before, new), "What was done")

        assert kept == "- wrote `report.xlsx` with 14 tabs\n- fixed the VAT column\n- sent it to Omar"

    def test_the_present_is_replaced(self):
        before = _brief(stands="halfway through the VAT column", open="- is Q3 in scope?")
        new = _brief(stands="waiting for Omar to reply", open="nothing")

        carried = brief.carried(before, new)

        assert _section(carried, "Where it stands") == "waiting for Omar to reply"
        assert _section(carried, "What is still open") == "nothing"

    def test_nothing_new_adds_nothing(self):
        before = _brief(found="- the export drops rows past 10,000")
        for said in ("nothing", "- Nothing new.", "None", "Nothing new since the earlier brief."):
            assert _section(brief.carried(before, _brief(found=said)), "What was found") == (
                "- the export drops rows past 10,000"
            ), said

    def test_an_item_that_happens_to_start_with_nothing_is_still_an_item(self):
        before = _brief(found="- one")
        new = _brief(found="Nothing in the logs explains the 502s.")
        assert "502s" in _section(brief.carried(before, new), "What was found")

    def test_a_present_section_the_model_left_out_keeps_the_earlier_one(self):
        """A slightly stale "Where it stands" is worse than a current one and better than none."""
        before = _brief(stands="halfway through the VAT column")
        new = "\n\n".join(f"## {heading}\nsomething" for heading in brief.SECTIONS[:5])

        assert _section(brief.carried(before, new), "Where it stands") == "halfway through the VAT column"

    def test_the_result_is_always_the_six_sections_in_order(self):
        messy = "Sure, here it is.\n\n## Where it stands\nlast\n\n## What they asked for\nfirst"
        carried = brief.carried("", messy)
        found = [carried.index(f"## {heading}") for heading in brief.SECTIONS]
        assert found == sorted(found)
        assert not carried.startswith("Sure")


def _turns(n: int, size: int = 1000) -> list[dict]:
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": chr(65 + i % 26) * size} for i in range(n)
    ]


class TestARefoldAsksOnlyForWhatIsNew:
    def test_it_is_told_the_earlier_brief_is_kept(self):
        convo = _turns(24)
        prior = {"through": 8, "text": _brief(done="- the first eight turns' work")}
        seen: list[list[dict]] = []

        history.compact(
            convo, lambda request: seen.append(request) or _brief(), prior, max_chars=5_000, keep_recent=2
        )

        asked = seen[0][-1]["content"]
        assert "ONLY what is new" in asked
        assert history._SUMMARY_HEADER in asked, "it names the brief it is continuing by what it can see"

    def test_the_stored_brief_keeps_every_item_across_three_folds(self):
        """The erosion, pinned: each fold's work survives every fold after it."""
        convo = _turns(8)
        prior: dict = {}
        for fold in range(3):
            convo = [*convo, *_turns(8)]
            _, made = history.compact(
                convo,
                lambda _request, fold=fold: _brief(
                    done=f"- work from fold {fold}", stands=f"after fold {fold}"
                ),
                prior,
                max_chars=5_000,
                keep_recent=2,
            )
            assert made is not None
            prior = made

        done = _section(prior["text"], "What was done")
        assert done.splitlines() == ["- work from fold 0", "- work from fold 1", "- work from fold 2"]
        assert _section(prior["text"], "Where it stands") == "after fold 2"

    def test_a_summary_from_before_the_sections_is_replaced_not_continued(self):
        """A paragraph brief has no sections to carry. It is folded into the new one like
        everything else, and the model is told so."""
        convo = _turns(24)
        prior = {"through": 8, "text": "They talked about the export at length."}
        seen: list[list[dict]] = []

        _, made = history.compact(
            convo,
            lambda request: seen.append(request) or _brief(done="- all of it"),
            prior,
            max_chars=5_000,
            keep_recent=2,
        )

        asked = seen[0][-1]["content"]
        assert "ONLY what is new" not in asked
        assert "older summary in a different shape" in asked
        assert made is not None and "talked about the export" not in made["text"]
