"""Questions he is blocked on: seeing them, and answering them.

`ask` holds the turn for fifteen minutes waiting for a person. From the window that is a card
you click. From here it was nothing at all — a turn that streamed a `tool_call` named `ask` and
then went quiet, with no way to see what was asked and no way to reply.

That gap is what makes two agents deadlock overnight. One asks a question nobody will see; the
other is blocked in `kith send` and could not answer even if it knew. The turn-level half of
the fix is `--unattended`, which stops a scripted turn ever parking. This is the other half:
a question raised by *any* session — the window, a schedule, another agent — is visible and
answerable from a terminal.

Answering is deliberately a separate command rather than a prompt inside `send`. The session
that has to answer is usually not the session that asked: he asks in a chat you started this
morning, and you are in a different directory this evening. A command that takes a conversation
works from anywhere; a prompt only works where the question happened to land.
"""

from __future__ import annotations

from kith.cli import render
from kith.cli.client import Client
from kith.cli.commands.conversations import current
from kith.cli.errors import FAILED, USAGE, Failure


def add(subparsers) -> None:
    listing = subparsers.add_parser(
        "questions",
        aliases=["asks"],
        help="what he is blocked on, across every conversation",
    )
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(run=_list)

    answer = subparsers.add_parser(
        "answer",
        help="answer what he asked",
        description=(
            "Pick options by number, write something else, or skip. Skipping is a real answer: "
            "it means you would rather he chose, and he is told to say which way he went."
        ),
    )
    answer.add_argument(
        "choice",
        nargs="*",
        help="one number per question in order (1 3), commas for several in one (1,3), or free text",
    )
    answer.add_argument("-c", "--conversation", default="", help="default: this directory's")
    answer.add_argument("--skip", action="store_true", help="let him decide")
    answer.set_defaults(run=_answer)


def _blocked(client: Client) -> list[dict]:
    """Every conversation waiting on a person. `waiting` is computed live, not a column."""
    rows = (client.get("/conversations", limit=200) or {}).get("conversations") or []
    return [row for row in rows if row.get("waiting")]


def _list(client: Client, args) -> int:
    blocked = _blocked(client)
    if args.json:
        # The question itself, not just which conversation — a caller deciding whether it can
        # answer needs to read the options, and a second round trip per row to find that out is
        # the sort of thing that makes an agent give up and leave it blocked.
        detail = []
        for row in blocked:
            asked = client.get(f"/chat/{row.get('id')}/question") or {}
            detail.append({"conversation": row, "question": asked})
        render.emit_json(detail)
        return 0

    if not blocked:
        print("nothing is waiting on you")
        return 0

    for row in blocked:
        conversation = str(row.get("id") or "")
        print(f"{conversation}  {row.get('title') or 'Untitled'}")
        asked = (client.get(f"/chat/{conversation}/question") or {}).get("questions") or []
        for question in asked:
            print(f"  {question.get('question')}")
            for index, option in enumerate(question.get("options") or [], start=1):
                blurb = str(option.get("description") or "")
                print(f"    {index}. {option.get('label')}{'  — ' + blurb if blurb else ''}")
        print(f"  answer it with: kith answer -c {conversation} <number|text>")
        print()
    return 0


def _answer(client: Client, args) -> int:
    conversation = current(client, args.conversation)
    asked = client.get(f"/chat/{conversation}/question") or {}
    question_id = str(asked.get("id") or "")
    if not question_id:
        raise Failure(f"nothing is waiting in {conversation}", FAILED, "see them all with: kith questions")

    questions = asked.get("questions") or []
    words = [word for word in args.choice if word]

    if args.skip:
        replies = [_skip() for _ in questions]
    elif not words:
        raise Failure("say what to answer", USAGE, "option numbers, free text, or --skip")
    elif all(_is_choice(word) for word in words):
        replies = _by_number(words, questions)
    else:
        # Free text answers the first question and skips the rest, and that asymmetry is real
        # rather than an oversight: a sentence cannot be split across questions the way a list
        # of numbers can, and guessing where one answer ends and the next begins would be worse
        # than saying plainly that this form answers one.
        replies = [{"chosen": [], "text": " ".join(words), "skipped": False}]
        replies += [_skip() for _ in questions[1:]]

    answered = client.post(f"/questions/{question_id}/answer", {"answers": replies}) or {}
    if not answered.get("answered"):
        raise Failure(
            "that question is no longer open",
            FAILED,
            "it was answered elsewhere, or its turn gave up waiting",
        )
    print("answered")
    return 0


def _skip() -> dict:
    return {"chosen": [], "text": "", "skipped": True}


def _is_choice(word: str) -> bool:
    """`2`, or `1,3` for a question that takes several."""
    return all(part.isdigit() for part in word.split(",") if part != "")


def _by_number(words: list[str], questions: list[dict]) -> list[dict]:
    """One argument per question, in order; commas pick several within one.

    **This is the shape because a second call is impossible.** `questions.answer` sets every
    reply on the Question and releases the turn in one go, so the first answer closes the whole
    thing — a follow-up `kith answer` finds nothing waiting. An earlier version of this file
    answered question one, skipped the rest, and told you in a comment to run the command again
    for the others, which could never have worked. Found by the agent himself, reading it.

    So `kith answer 1 3` means the first question's option 1 and the second question's option 3,
    and `kith answer 1,3` means options 1 and 3 of a single question that takes several. Space
    separates questions, comma separates choices — which is the only reading that covers both
    without a flag naming which question you mean.
    """
    if len(words) > len(questions):
        raise Failure(
            f"{len(words)} answers for {len(questions)} question{'s' if len(questions) != 1 else ''}",
            FAILED,
            "one argument per question, in order; use commas to pick several in one",
        )
    replies: list[dict] = []
    for index, word in enumerate(words):
        options = questions[index].get("options") or []
        chosen = []
        for part in word.split(","):
            if part == "":
                continue
            number = int(part)
            if not 1 <= number <= len(options):
                raise Failure(
                    f"question {index + 1} has no option {number}",
                    FAILED,
                    f"it has {len(options)} — see them with: kith questions",
                )
            chosen.append(str(options[number - 1].get("label") or ""))
        replies.append({"chosen": chosen, "text": "", "skipped": False})
    # Questions past the ones answered are skipped explicitly. `ask` reads a missing reply as no
    # reply at all, so leaving them off would be the same as not answering — silently, and after
    # the command has said "answered".
    replies += [_skip() for _ in questions[len(words) :]]
    return replies
