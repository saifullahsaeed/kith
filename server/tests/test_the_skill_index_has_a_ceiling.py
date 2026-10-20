"""The whole skill index has a budget, not just the shipped part of it.

`test_the_shipped_index_stays_small` caps the *bundled* skills at 600 tokens together, and it
works: the five that ship measure 423. But it is a cap on the folder this repository controls,
and the index is not built from that folder — it is built from everything installed. On a real
machine that is 24 skills, of which 19 arrived from somewhere else and total 8,317 characters.

So 83% of the text in the cached prefix is governed by nothing at all, and the number only goes
one way: every skill anyone installs adds to a block that is read on every request of every
turn, forever, and nothing anywhere says stop.

This is the same defect as the seven uncapped sources in the present-state block, one level up,
and it gets the same answer: one budget, and a rule for what happens to what does not fit.

**What does not fit degrades to its name rather than disappearing.** Dropping a skill from the
index makes it genuinely unreachable — `read_skill` takes a name, and a name he has never seen
is a name he cannot call. Keeping the name costs a few characters and preserves the one thing
that matters, which is that he knows it exists. This also honours the trade recorded in
f2bcf64: a fuller description triggers more reliably, so the skills that fit keep theirs in
full rather than every description being shortened to make room.

**Order has to be stable.** This text sits in the cached prefix, so an ordering that varies
between requests would move the seam and re-bill the persona in front of it — the failure
`llm/caching` and `turn/prompt` both carry measurements about.
"""

from __future__ import annotations

from pathlib import Path

from kith.services import skills


def _skill(name: str, description: str) -> skills.Skill:
    return skills.Skill(name=name, description=description, path=Path("/tmp") / name)


def _many(count: int, description_chars: int = 900) -> list[skills.Skill]:
    return [_skill(f"skill-{i:03d}", "d" * description_chars) for i in range(count)]


class TestTheIndexIsBounded:
    def test_a_reasonable_number_of_skills_all_keep_their_descriptions(self):
        listed = skills.render_index(_many(4, 300))
        # Count entry lines that carry a description; the d's repeat and the preamble has
        # an em dash of its own, so neither is a safe thing to count.
        described = [ln for ln in listed.splitlines() if ln.startswith("- **") and "—" in ln]
        assert len(described) == 4
        assert "name only" not in listed

    def test_a_machine_full_of_skills_cannot_grow_the_prefix_without_limit(self):
        assert len(skills.render_index(_many(200))) <= skills.INDEX_BUDGET_CHARS

    def test_nothing_is_lost_even_when_almost_nothing_fits(self):
        """Every installed skill is still named, however many there are. This is the property
        that makes trimming safe rather than a silent capability loss."""
        listed = skills.render_index(_many(200))
        for i in range(200):
            assert f"skill-{i:03d}" in listed


class TestWhatSurvivesInFull:
    def test_the_ones_that_fit_keep_the_whole_description(self):
        """Not every description shortened to make room — see f2bcf64. A description that
        triggers is worth more than three that nearly do."""
        listed = skills.render_index(_many(200))
        assert "d" * 900 in listed

    def test_a_skill_reduced_to_its_name_carries_no_description(self):
        listed = skills.render_index(_many(200))
        last = listed.rstrip().splitlines()[-1]
        assert last == "- **skill-199**"  # the name, and nothing after it

    def test_the_reader_is_told_the_tail_is_names_only(self):
        """Otherwise a bare name reads as a skill whose author wrote no description, and he has
        no reason to spend a `read_skill` call finding out."""
        assert "name" in skills.render_index(_many(200)).lower()


class TestItStaysCacheable:
    def test_the_same_skills_render_identically(self):
        some = _many(30)
        assert skills.render_index(some) == skills.render_index(list(some))

    def test_the_order_does_not_depend_on_the_order_they_were_found_in(self):
        """`installed()` walks directories, and directory order is not guaranteed between runs
        or between machines. If that reached the prompt, the cached prefix would break for
        reasons nobody could see."""
        some = _many(30)
        assert skills.render_index(some) == skills.render_index(list(reversed(some)))


class TestTheRealInstall:
    def test_this_machine_is_inside_the_budget(self):
        assert len(skills.index()) <= skills.INDEX_BUDGET_CHARS

    def test_an_empty_install_still_pays_nothing(self):
        assert skills.render_index([]) == ""


class TestWhichOnesKeepTheirDescription:
    """Alphabetical is stable, which the cache needs, and arbitrary about *what it sacrifices*,
    which nothing wants.

    Measured on the real install with a plain a-z sort: `docx` and `code-refactor` kept their
    descriptions while `test-driven-development`, `verification-before-completion`,
    `systematic-debugging` and `sending-workers` were cut to names. The last of those is one of
    the five skills this repository ships — the curated set, already budgeted by
    `test_the_shipped_index_stays_small`, and the set the persona and the other skills actually
    name by hand. Losing its trigger to make room for a spreadsheet reader is the wrong trade
    in every direction.

    So the bundled skills are paid first and the rest compete alphabetically behind them. Still
    fully deterministic, so the cached prefix is unaffected.
    """

    def test_a_bundled_skill_keeps_its_description_however_late_it_sorts(self):
        crowd = _many(200)
        mine = _skill("zzz-bundled", "the description that has to survive")
        listed = skills.render_index([*crowd, mine], first={"zzz-bundled"})
        assert "the description that has to survive" in listed

    def test_and_it_is_listed_before_the_crowd(self):
        crowd = _many(200)
        mine = _skill("zzz-bundled", "first among equals")
        listed = skills.render_index([*crowd, mine], first={"zzz-bundled"})
        assert listed.index("zzz-bundled") < listed.index("skill-000")

    def test_the_real_install_keeps_every_shipped_description(self):
        from kith import settings

        # The active agent strategy is not listed: its whole text is in the prompt already.
        shipped = {p.name for p in settings.BUNDLED_SKILLS_DIR.iterdir() if p.is_dir()} - {skills._strategy_name()}
        listed = skills.index()
        described = {
            line.split("**")[1] for line in listed.splitlines() if line.startswith("- **") and "—" in line
        }
        assert shipped <= described, f"shipped skills cut to a name: {sorted(shipped - described)}"
