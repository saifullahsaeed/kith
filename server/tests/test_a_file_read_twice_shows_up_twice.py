"""Which calls filled a category — and which of them were the same call again.

The ledger says "code he has read: 300k". That is the number, and it is not the answer: 300k of
files he needed once and 300k of the same file read sixty times are the same figure and
completely different problems. The first is the cost of the work; the second is waste you can
remove without losing anything, because every copy but the newest is describing a file as it was
before the edits that followed.

Measured on one real conversation in this repo before any of this existed: 3,241 code reads,
2.3M tokens of them repeats — one file read 64 times, `repo_map .` run 36 times. `ledger.take`
could see none of it, because it adds a tool result's characters to a bucket and drops
everything about which call produced them.

So: the same walk over the same list, keeping the call this time. Same list matters — itemising
a *differently* built view of the conversation would produce numbers that quietly disagree with
the meter, and two numbers that disagree are worse than one number with no detail.
"""

from __future__ import annotations

import json

from kith.llm import ledger


def _read(path: str, size: int) -> list[dict]:
    """A file read, in the shape `conversations.full_messages` rebuilds: the call's arguments
    ride on the assistant turn, the result on the `tool` message that follows it."""
    return [
        {
            "role": "assistant",
            "tool_calls": [{"function": {"name": "read_file", "arguments": {"path": path}}}],
        },
        {"role": "tool", "tool_name": "read_file", "content": "x" * size},
    ]


class TestItNamesWhatFilledTheCategory:
    def test_a_file_read_once_is_one_item_naming_the_file(self):
        convo = _read("src/app.tsx", 4_000)

        items = ledger.itemise(convo, chars_per_token=4.0)

        assert len(items) == 1
        assert items[0].tool == "read_file"
        assert items[0].subject == "src/app.tsx"
        assert items[0].calls == 1
        # Against `take` rather than against `4000 / 4`: a message costs its whole JSON envelope,
        # not the length of its content, and a hand-computed figure here would be asserting a
        # guess about `message_chars` instead of the agreement that matters.
        assert items[0].tokens == ledger.take(convo, chars_per_token=4.0).of("code")

    def test_the_same_file_read_twice_is_one_item_counted_twice(self):
        once = _read("src/app.tsx", 4_000)
        convo = [*once, *_read("src/app.tsx", 4_000)]

        items = ledger.itemise(convo, chars_per_token=4.0)

        assert len(items) == 1
        assert items[0].calls == 2
        assert items[0].tokens == ledger.take(convo, chars_per_token=4.0).of("code")
        assert items[0].tokens > ledger.take(once, chars_per_token=4.0).of("code")

    def test_two_different_files_stay_two_items(self):
        convo = [*_read("src/app.tsx", 4_000), *_read("src/other.tsx", 4_000)]

        items = ledger.itemise(convo, chars_per_token=4.0)

        assert {item.subject for item in items} == {"src/app.tsx", "src/other.tsx"}

    def test_the_same_path_read_by_a_different_tool_is_a_different_item(self):
        """`read_file` on a path and `outline` on the same path are not the same read, and
        collapsing them would report a duplicate that does not exist."""
        convo = [
            *_read("src/app.tsx", 4_000),
            {
                "role": "assistant",
                "tool_calls": [{"function": {"name": "outline", "arguments": {"path": "src/app.tsx"}}}],
            },
            {"role": "tool", "tool_name": "outline", "content": "y" * 400},
        ]

        items = ledger.itemise(convo, chars_per_token=4.0)

        assert {item.tool for item in items} == {"read_file", "outline"}


class TestItSaysWhatIsWaste:
    def test_a_file_read_once_wastes_nothing(self):
        items = ledger.itemise(_read("src/app.tsx", 4_000), chars_per_token=4.0)

        assert items[0].wasted == 0

    def test_only_the_newest_copy_is_worth_keeping(self):
        """Every earlier copy describes the file as it was before whatever happened since. The
        newest is the one that might still be true, so it is the one that is not waste."""
        older = _read("src/app.tsx", 4_000)
        newer = _read("src/app.tsx", 1_000)

        items = ledger.itemise([*older, *newer], chars_per_token=4.0)

        # What is left after removing the waste is exactly the newest read, and the waste is
        # exactly the older one.
        assert items[0].wasted == ledger.take(older, chars_per_token=4.0).of("code")
        assert items[0].tokens - items[0].wasted == ledger.take(newer, chars_per_token=4.0).of("code")

    def test_the_worst_offender_sorts_first(self):
        """Sorted by what you would get back, not by total: a 300k file read once is the cost of
        the work and nothing to act on, while 60 copies of a 5k file is the thing to remove."""
        convo = [
            *_read("huge-but-read-once.tsx", 40_000),
            *_read("small.tsx", 4_000),
            *_read("small.tsx", 4_000),
            *_read("small.tsx", 4_000),
        ]

        items = ledger.itemise(convo, chars_per_token=4.0)

        assert items[0].subject == "small.tsx"
        assert items[0].wasted > items[1].wasted


class TestItSortsIntoTheSameCategoriesTheMeterShows:
    def test_a_file_read_belongs_to_code(self):
        items = ledger.itemise(_read("src/app.tsx", 4_000), chars_per_token=4.0)

        assert items[0].key == "code"

    def test_a_skill_belongs_to_skills(self):
        convo = [
            {
                "role": "assistant",
                "tool_calls": [
                    {"function": {"name": "read_skill", "arguments": {"name": "running-a-project"}}}
                ],
            },
            {"role": "tool", "tool_name": "read_skill", "content": "s" * 2_000},
        ]

        items = ledger.itemise(convo, chars_per_token=4.0)

        assert items[0].key == "skills"

    def test_anything_else_belongs_to_other_tool_results(self):
        convo = [
            {
                "role": "assistant",
                "tool_calls": [{"function": {"name": "remember", "arguments": {"text": "hi"}}}],
            },
            {"role": "tool", "tool_name": "remember", "content": "ok" * 500},
        ]

        items = ledger.itemise(convo, chars_per_token=4.0)

        assert items[0].key == "tool_results"

    def test_every_category_total_matches_the_ledger_it_subdivides(self):
        """The whole reason to itemise the same list `take` walks. If these two ever disagree,
        the detail screen is contradicting the meter in the rail beside it, and there is no way
        for the person to tell which one is lying."""
        convo = [
            *_read("a.tsx", 4_000),
            *_read("a.tsx", 2_000),
            *_read("b.tsx", 6_000),
            {
                "role": "assistant",
                "tool_calls": [{"function": {"name": "remember", "arguments": {"text": "x"}}}],
            },
            {"role": "tool", "tool_name": "remember", "content": "z" * 800},
        ]

        book = ledger.take(convo, window=1_000_000, chars_per_token=4.0)
        items = ledger.itemise(convo, chars_per_token=4.0)

        for key in ("code", "tool_results", "skills"):
            assert sum(i.tokens for i in items if i.key == key) == book.of(key), key

    def test_they_still_match_when_the_sizes_do_not_divide_evenly(self):
        """`take` converts a category's characters to tokens once, at the end. Converting each
        item separately and adding those up is a different sum — three results of 4,003
        characters are 3,002 tokens counted together and 3,000 counted apart. Real reads are
        never round numbers, so this is the ordinary case, not the edge one."""
        convo = [
            *_read("a.tsx", 4_003),
            *_read("b.tsx", 4_003),
            *_read("c.tsx", 4_003),
        ]

        book = ledger.take(convo, window=1_000_000, chars_per_token=4.0)
        items = ledger.itemise(convo, chars_per_token=4.0)

        assert sum(i.tokens for i in items) == book.of("code")


class TestItOnlyClaimsARepeatItCanProve:
    """Found by running this over a real conversation rather than over its fixtures: the top row
    was `read_skill ×191, 72,689 wasted` with no subject on it. Those were 191 *different* skills.
    `read_skill` takes `name`, which was not in the list of arguments this looked at, so every one
    of them grouped under the same empty subject and the screen's headline number was a fiction.

    Two calls whose subject cannot be read are not known to be the same call, and the whole value
    of this screen is that its waste figure is one you can act on without checking it first.
    """

    def _unnamed(self, tool: str, size: int) -> list[dict]:
        return [
            {"role": "assistant", "tool_calls": [{"function": {"name": tool, "arguments": {}}}]},
            {"role": "tool", "tool_name": tool, "content": "x" * size},
        ]

    def test_calls_with_no_readable_subject_are_never_called_waste(self):
        convo = [*self._unnamed("edit_files", 4_000), *self._unnamed("edit_files", 4_000)]

        items = ledger.itemise(convo, chars_per_token=4.0)

        assert sum(item.wasted for item in items) == 0

    def test_they_are_still_counted_so_the_category_adds_up(self):
        """Honest about what it cannot say, without going quiet: the tokens are in the window
        whether or not the call can be named."""
        convo = [*self._unnamed("edit_files", 4_000), *self._unnamed("edit_files", 4_000)]

        book = ledger.take(convo, chars_per_token=4.0)
        items = ledger.itemise(convo, chars_per_token=4.0)

        assert sum(item.tokens for item in items) == book.of("tool_results")

    def test_a_skill_is_named_by_the_argument_it_actually_takes(self):
        convo = [
            {
                "role": "assistant",
                "tool_calls": [
                    {"function": {"name": "read_skill", "arguments": {"name": "running-a-project"}}}
                ],
            },
            {"role": "tool", "tool_name": "read_skill", "content": "s" * 2_000},
        ]

        items = ledger.itemise(convo, chars_per_token=4.0)

        assert items[0].subject == "running-a-project"

    def test_two_different_skills_are_two_items(self):
        def skill(name: str) -> list[dict]:
            return [
                {
                    "role": "assistant",
                    "tool_calls": [{"function": {"name": "read_skill", "arguments": {"name": name}}}],
                },
                {"role": "tool", "tool_name": "read_skill", "content": "s" * 2_000},
            ]

        items = ledger.itemise([*skill("writing-plans"), *skill("running-a-project")], chars_per_token=4.0)

        assert len(items) == 2
        assert sum(item.wasted for item in items) == 0

    def test_a_subject_that_is_not_text_still_names_the_call(self):
        """`view_task` takes an `id`, and an id arrives as a number. Left as "not a string" it
        fell through to no-subject, so every task he opened grouped together."""

        def task(number: int) -> list[dict]:
            return [
                {
                    "role": "assistant",
                    "tool_calls": [{"function": {"name": "view_task", "arguments": {"id": number}}}],
                },
                {"role": "tool", "tool_name": "view_task", "content": "t" * 2_000},
            ]

        items = ledger.itemise([*task(11), *task(12)], chars_per_token=4.0)

        assert {item.subject for item in items} == {"11", "12"}


class TestOnlyALookCanBeSuperseded:
    """Found by looking at the finished screen against a real conversation. The top row was
    `edit_file ×10` on one file, priced at 2.5k "you could drop without losing anything". You
    could not: those were ten different edits, with ten different results, and nine of them are
    not copies of the tenth. The same went for `update_task ×5` and `add_checklist ×6`.

    Reading a file twice and editing it twice are not the same shape. A read is a *look* at
    something that has a current state, so a later look supersedes an earlier one — the earlier
    copy describes a file that has since changed, which is worse than not having it. An edit is
    an *act*; the second one does not make the first redundant, it happened too.

    So waste is only ever claimed for calls that look. Everything else is still counted and still
    shows its call count, which is worth seeing — it just is not called removable.
    """

    def _calls(self, tool: str, subject_key: str, subject: str, size: int, times: int) -> list[dict]:
        convo: list[dict] = []
        for _ in range(times):
            convo += [
                {
                    "role": "assistant",
                    "tool_calls": [{"function": {"name": tool, "arguments": {subject_key: subject}}}],
                },
                {"role": "tool", "tool_name": tool, "content": "x" * size},
            ]
        return convo

    def test_editing_one_file_ten_times_is_not_ten_copies_of_anything(self):
        convo = self._calls("edit_file", "path", "src/App.tsx", 2_000, 10)

        items = ledger.itemise(convo, chars_per_token=4.0)

        assert items[0].calls == 10, "the count is still worth seeing"
        assert items[0].wasted == 0, "but nine edits are not nine stale copies of the tenth"

    def test_reading_one_file_ten_times_still_is(self):
        convo = self._calls("read_file", "path", "src/App.tsx", 2_000, 10)

        items = ledger.itemise(convo, chars_per_token=4.0)

        assert items[0].wasted > 0

    def test_a_task_updated_five_times_is_five_things_that_happened(self):
        convo = self._calls("update_task", "id", "105", 2_000, 5)

        items = ledger.itemise(convo, chars_per_token=4.0)

        assert items[0].wasted == 0

    def test_a_shell_command_run_twice_is_not_assumed_to_be_a_look(self):
        """`ls` is a look and `rm -rf` is not, and the arguments cannot tell them apart. When it
        cannot be known, the safe direction is to claim nothing — an overstated waste figure is
        one the person has to go and check, which is worse than no figure."""
        convo = self._calls("shell", "command", "pnpm dev", 2_000, 2)

        items = ledger.itemise(convo, chars_per_token=4.0)

        assert items[0].wasted == 0

    def test_a_skill_re_read_is_a_look(self):
        convo = self._calls("read_skill", "name", "writing-plans", 2_000, 4)

        items = ledger.itemise(convo, chars_per_token=4.0)

        assert items[0].wasted > 0

    def test_the_mutating_calls_are_still_counted_toward_the_category(self):
        """Refusing to call them waste must not make them disappear — the tokens are in the
        window and the categories still have to add up."""
        convo = self._calls("edit_file", "path", "src/App.tsx", 2_000, 3)

        book = ledger.take(convo, chars_per_token=4.0)
        items = ledger.itemise(convo, chars_per_token=4.0)

        assert sum(item.tokens for item in items) == book.of("tool_results")


class TestTheShapesItHasToSurvive:
    def test_arguments_that_arrive_as_json_text_still_name_the_file(self):
        """`full_messages` hands back dicts; `_to_openai` turns them into strings on the way to
        a provider. Both shapes reach this, and a string that silently fell through would blank
        every subject on exactly the path a real provider request takes."""
        convo = [
            {
                "role": "assistant",
                "tool_calls": [{"function": {"name": "read_file", "arguments": '{"path": "src/app.tsx"}'}}],
            },
            {"role": "tool", "tool_name": "read_file", "content": "x" * 4_000},
        ]

        items = ledger.itemise(convo, chars_per_token=4.0)

        assert items[0].subject == "src/app.tsx"

    def test_a_result_with_no_call_in_front_of_it_is_still_counted(self):
        """A fold can cut a conversation between an assistant turn and its result. The tokens
        are real and still in the window, so dropping the item would make the itemised total
        disagree with the category it belongs to."""
        convo = [{"role": "tool", "tool_name": "read_file", "content": "x" * 4_000}]

        items = ledger.itemise(convo, chars_per_token=4.0)

        assert len(items) == 1
        assert items[0].subject == ""
        assert items[0].tokens == ledger.take(convo, chars_per_token=4.0).of("code")

    def test_a_conversation_with_no_tool_results_itemises_to_nothing(self):
        convo = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]

        assert ledger.itemise(convo, chars_per_token=4.0) == ()

    def test_it_survives_the_wire(self):
        convo = _read("src/app.tsx", 4_000)
        items = ledger.itemise(convo, chars_per_token=4.0)

        assert ledger.items_as_wire(items) == [
            {
                "key": "code",
                "tool": "read_file",
                "subject": "src/app.tsx",
                "calls": 1,
                "tokens": ledger.take(convo, chars_per_token=4.0).of("code"),
                "wasted": 0,
            }
        ]
        assert json.loads(json.dumps(ledger.items_as_wire(items))) == ledger.items_as_wire(items)
