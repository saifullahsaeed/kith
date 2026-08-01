"""Everything the control panel offers to create, it can actually create.

Two of its forms had been dead the whole time. Reminders and Schedules each had an Add
button, each posted to this registry, and the registry had no ``add`` for either — so the
answer was 400 "cannot add a reminder". A button that looks like a feature and is a dead end,
and nothing in the suite noticed, because nothing tested the panel's own path.

So these are shaped like the panel rather than like the repositories: post the payload the
form actually sends, then ask whether the thing appears in the snapshot the panel re-reads
afterwards. Both halves matter — a create that succeeds and does not show up is, from the
other side of the screen, the same bug as one that fails.
"""

from __future__ import annotations

import pytest

from kith.services import brain
from kith.services.brain.kinds import KINDS

#: What each of the panel's create forms posts. Taken from the `create(...)` calls in
#: control-panel.tsx rather than invented here — exercising the real shapes is the point.
FORMS: dict[str, tuple[dict, str]] = {
    "memory": ({"content": "a thing worth keeping", "tags": ["probe"]}, "memories"),
    "note": ({"title": "A note", "body": "its body"}, "notes"),
    "person": ({"name": "Probe Person"}, "people"),
    "project": ({"name": "A project", "description": "its point"}, "projects"),
    "task": ({"goal": "do the thing", "description": "how"}, "tasks"),
    # The two that were broken.
    "reminder": ({"note": "look at this later", "in_minutes": 30}, "reminders"),
    "schedule": ({"note": "every hour", "every_minutes": 60}, "schedules"),
}


@pytest.mark.parametrize("kind", sorted(FORMS))
def test_the_panel_can_create_it_and_then_see_it(db, kind):
    payload, bucket = FORMS[kind]

    made = brain.create(db, kind, payload)

    assert made and made.get("id"), f"creating a {kind} returned nothing usable"
    listed = brain.snapshot(db)[bucket]
    assert any(str(row["id"]) == str(made["id"]) for row in listed), (
        f"a new {kind} did not appear in snapshot['{bucket}'] — the panel re-reads that, "
        "so as far as anyone using the app is concerned it was not created"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"note": "every hour", "every_minutes": 60},
        {"note": "each morning", "daily_at": "09:00"},
    ],
)
def test_both_ways_the_schedule_form_can_be_filled(db, payload):
    made = brain.create(db, "schedule", payload)
    # Whichever way it was spelled, it has to come back knowing when it happens next. A
    # schedule with no next time is a row, not a schedule.
    assert made.get("next_fire")


def test_a_reminder_gets_an_actual_time(db):
    made = brain.create(db, "reminder", {"note": "in a bit", "in_minutes": 45})
    assert made.get("fire_at"), "a reminder with no time will never fire"


def test_a_reminder_with_no_note_is_refused(db):
    # The form can be submitted empty. Refusing beats storing a nameless reminder that fires
    # later and says nothing.
    with pytest.raises(ValueError):
        brain.create(db, "reminder", {"in_minutes": 5})


class TestTheRegistryAndThePanelHaveToAgree:
    """The general shape of the original bug.

    The panel decides what to offer from its own code; the server decides what it accepts
    from this registry. When the two drift, the symptom is a button that 400s.
    """

    @pytest.mark.parametrize("kind", ["memory", "note", "person", "project", "task", "reminder", "schedule"])
    def test_every_kind_with_a_form_is_creatable(self, kind):
        assert KINDS[kind].add is not None, f"the panel has an Add form for {kind}"

    def test_an_unknown_kind_names_what_is_known(self, db):
        with pytest.raises(ValueError) as caught:
            brain.create(db, "nonsense", {})
        # "unknown kind" and "that kind cannot be created" are different problems and the
        # error distinguishes them, which is how the dead forms were finally identified.
        assert "unknown kind" in str(caught.value)

    def test_a_kind_that_genuinely_cannot_be_created_says_that_instead(self, db):
        # His journal is his. The refusal is correct here — what was wrong was reminders and
        # schedules giving the same answer while having a form in front of them.
        with pytest.raises(ValueError) as caught:
            brain.create(db, "journal", {"entry": "not yours to write"})
        assert "cannot" in str(caught.value)
