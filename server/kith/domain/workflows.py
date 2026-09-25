"""What opts a turn into writing a workflow: the message starts with ``/workflow``.

The whole opt-in is the person's own text. That was chosen over a request flag because every
property that matters falls out of it for free: the transcript records it, the model reads it in
the message it is answering, the command line gets it by typing it, and a turn the scheduler
opens can never carry it — its opening line is written by the scheduler, not a person.
"""

from __future__ import annotations

PREFIX = "/workflow"


def asks_for_workflow(message: str) -> bool:
    """Does this message open with the ``/workflow`` prefix, as a word of its own?

    ``/workflows`` or ``/workflowish`` is not the prefix; ``/workflow`` alone, or followed by
    whitespace, is. Case-insensitive, because a phone keyboard capitalises the first letter.
    """
    text = str(message or "").lstrip()
    if not text.lower().startswith(PREFIX):
        return False
    rest = text[len(PREFIX) :]
    return not rest or rest[0].isspace()
