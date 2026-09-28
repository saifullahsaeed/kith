"""A wake sits in the person's slot, and the fold must not mistake it for them.

A reminder firing, a background run reporting back — the harness starting a turn — is stored as
`system` and replayed as `user`, because a request has to end in something to answer. So to
everything that reads the replayed history it looked exactly like the person speaking, and the
fold reads it twice: for the questions it carries forward word for word, and for the recent
turns it keeps whole.

Measured on real conversations: wakes were 94% of the characters one fold carried as "what they
asked", which spent the ceiling on the harness and dropped the person's oldest requests first.
And at one fold the protected tail was five wakes and the new message, so the person's own last
request — one turn further back — was folded into a summary while a reminder's prose stayed
verbatim.

`conversations.full_messages` marks every replayed wake `_wake: True`; these build that shape
directly.
"""

from __future__ import annotations

from kith.services import history


def _person(said: str) -> list[dict]:
    return [{"role": "user", "content": said}, {"role": "assistant", "content": f"re: {said}"}]


def _wake(said: str) -> list[dict]:
    return [{"role": "user", "content": said, "_wake": True}, {"role": "assistant", "content": "noted"}]


class TestWhatTheyAskedIsOnlyThem:
    def test_a_wake_is_not_carried_as_something_they_asked(self):
        convo = [
            *_person("check the VAT column"),
            *_wake("One of your reminders just fired"),
            *_person("and Q3?"),
        ]

        carried = [m["content"] for m in history._what_they_asked(convo, len(convo))]

        assert carried == ["check the VAT column", "and Q3?"]

    def test_wakes_cannot_crowd_their_words_out_of_the_ceiling(self):
        convo = [*_person("the one real request")]
        for _ in range(200):
            convo += _wake("A background run finished: " + "x" * 1_000)

        carried = [m["content"] for m in history._what_they_asked(convo, len(convo))]

        assert carried == ["the one real request"]

    def test_only_their_words_are_carried(self):
        """A message replays with the present-state blocks it was sent with (`_present`). Carried
        past a fold, those would come back beside every copied question — the clock and the
        project board of every day the conversation ran."""
        convo = [{"role": "user", "content": "check it", "_present": ["the clock at 09:00"]}]

        assert history._what_they_asked(convo, 1) == [{"role": "user", "content": "check it"}]


class TestTheRecentTurnsAreCountedInTheirTurns:
    def test_five_wakes_do_not_push_their_last_request_out_of_the_tail(self):
        """The measured case. With two of their turns protected, the last two are what they
        typed — and the wakes between them stay, because they happened inside that stretch."""
        convo = [*_person("an old question"), *_person("their real last request")]
        for n in range(5):
            convo += _wake(f"reminder {n}")
        convo += _person("so, where are we?")

        cut = history._turn_aware_cut(convo, keep_recent=2)

        assert convo[cut]["content"] == "their real last request"

    def test_the_turn_being_answered_is_kept_even_when_it_is_a_wake(self):
        convo = [*_person("one"), *_person("two"), *_wake("a background run finished")]

        cut = history._turn_aware_cut(convo, keep_recent=1)

        assert cut <= len(convo) - 2, "the wake that opened this turn is still in the tail"
        assert convo[cut]["content"] == "two", "and one of their own turns with it"

    def test_the_fold_itself_keeps_their_request_verbatim(self):
        convo = [*_person("A" * 2_000), *_person("their real last request " + "B" * 2_000)]
        for n in range(5):
            convo += _wake(f"reminder {n} " + "C" * 2_000)
        convo += _person("so, where are we?")

        kept, made = history.compact(convo, lambda _request: "the brief", max_chars=1, keep_recent=2)

        assert made is not None
        tail = kept[1 + len(history._what_they_asked(convo, made["through"])) :]
        assert tail[0]["content"].startswith("their real last request")
