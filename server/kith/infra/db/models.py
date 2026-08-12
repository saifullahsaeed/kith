"""SQLAlchemy models for Kith's brain.

These mirror the schema that ``migrations.py`` builds — they do **not** define it.
The migration list stays the single source of truth for DDL, so there is exactly
one thing to read to know what the database looks like, and no chance of models
and migrations drifting apart. ``create_all`` is never called against a live
database; models exist to query and write, not to build.

Types follow SQLite's actual storage, quirks included: timestamps are ISO-8601
``TEXT`` (not ``DateTime``) because that is what is already stored and what the
API returns; booleans are ``INTEGER`` 0/1; ``tags`` is a JSON string; embeddings
are packed float arrays in a ``BLOB``.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Float, Integer, LargeBinary, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    importance: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[str] = mapped_column(Text, nullable=False, default="recall")
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class JournalEntry(Base):
    __tablename__ = "journal"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="planning")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(Text, nullable=False, default="normal")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: The conversation working this task, while it is being worked. Set when the status
    #: becomes `working`, cleared when it leaves — see `update_task`. Null for everything
    #: started before there was anywhere to record it, and for a reminder firing unattended.
    conversation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(Text, nullable=False, default="kith")
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    milestone_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class TaskComment(Base):
    __tablename__ = "task_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False)
    author: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class ChecklistItem(Base):
    __tablename__ = "checklist_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class Deliverable(Base):
    __tablename__ = "deliverables"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="text")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    #: Where the work actually is. None for a project that is not code — research, a
    #: shortlist — which should not be handed a pretend folder.
    directory: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class Milestone(Base):
    __tablename__ = "milestones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    target_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="todo")
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Where the node sits once someone has arranged the graph. None means nobody has, and
    #: the interface lays it out — a stored 0,0 could not be told apart from a deliberate
    #: top-left corner.
    x: Mapped[float | None] = mapped_column(Float, nullable=True)
    y: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class MilestoneDep(Base):
    """One edge of a project's roadmap: `milestone_id` waits for `depends_on_id`.

    This is what makes a milestone more than a label. A milestone with an unfinished
    predecessor is not available, and neither are the tasks under it — so the graph decides
    what he works on next instead of raw task priority deciding it.
    """

    __tablename__ = "milestone_deps"

    milestone_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    depends_on_id: Mapped[int] = mapped_column(Integer, primary_key=True)


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fire_at: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    #: Which chat asked to be reminded, or None for one set outside any conversation. See
    #: `kith.services.scheduler.fire_due` — this is what lets firing report
    #: back to the actual chat rather than to whichever session happened to be open.
    conversation_id: Mapped[str | None] = mapped_column(Text, nullable=True)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    read: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    #: What sort of thing this is — note, asked, stuck, delivered, reachout, or user.
    #: Decides whether it is allowed to interrupt; see kith.services.notify.
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="note")
    sender: Mapped[str] = mapped_column(Text, nullable=False, default="kith")
    link: Mapped[str | None] = mapped_column(Text, nullable=True)


class Person(Base):
    __tablename__ = "people"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # UNIQUE COLLATE NOCASE in the live schema — case-insensitive by design, so
    # "saif" and "Saif" are the same person.
    name: Mapped[str] = mapped_column(String(collation="NOCASE"), nullable=False, unique=True)
    relationship: Mapped[str] = mapped_column(Text, nullable=False, default="")
    profile: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


# `Curiosity` was mapped here — the last piece of the scheduled inner life still standing.
# The tools, the repository and the brain kind all went; the ORM class stayed,
# referenced by nothing, still declaring `curiosities` to SQLAlchemy's metadata.
#
# The migration that creates the table stays where it is, and must: migrations are history,
# and rewriting one breaks every database that already ran it. An existing install keeps an
# empty table it no longer opens, which costs nothing. A mapped class is different — it is a
# live claim about what the schema is for, and this one had not been true for a day.


class SelfModel(Base):
    """Singleton row — who he thinks he's become."""

    __tablename__ = "self"
    __table_args__ = (CheckConstraint("id = 1"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    identity: Mapped[str] = mapped_column(Text, nullable=False, default="")
    profile: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class Mood(Base):
    """Singleton row — how he feels right now. Tints the whole UI."""

    __tablename__ = "mood"
    __table_args__ = (CheckConstraint("id = 1"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    label: Mapped[str] = mapped_column(Text, nullable=False, default="settling in")
    energy: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)


class SourceChunk(Base):
    __tablename__ = "source_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(Integer, nullable=False)
    ord: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    every_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_fire: Mapped[str] = mapped_column(Text, nullable=False)
    last_fired: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    #: Same as `Reminder.conversation_id` — which chat this standing job reports back to.
    conversation_id: Mapped[str | None] = mapped_column(Text, nullable=True)


class CustomTool(Base):
    """Keyed by name, not an id — he calls tools by name, so that is the identity."""

    __tablename__ = "custom_tools"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    parameters: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    required: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    language: Mapped[str] = mapped_column(Text, nullable=False, default="python")
    code: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class Conversation(Base):
    """One chat, indexed. The words are a JSONL file in the workspace, not a column."""

    __tablename__ = "conversations"

    #: A sortable timestamp plus a short suffix, so the folder reads chronologically in
    #: Finder as well as in the app. Also the transcript's filename.
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    #: OpenRouter stickiness, per conversation: its prefix is shared across its own turns
    #: and with nothing else, which is exactly the unit that wants one warm cache.
    session_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: What this session is working on. None for a conversation that is only a conversation.
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Whether he takes the next step on his own when this one finishes. What roaming used
    #: to be, scoped to the session instead of to the whole machine.
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    messages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class TurnLog(Base):
    """One row per turn — the durable flight recorder.

    Named for a background loop once, and never only about it: a chat turn records its cost here
    too, which is most of what the money dashboard reads.
    """

    __tablename__ = "turn_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    focus: Mapped[str | None] = mapped_column(Text, nullable=True)
    tools: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Prompt tokens with cache hits removed. None on rows written before v20.
    tokens_uncached: Mapped[int | None] = mapped_column(Integer, nullable=True)
    seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    outcome: Mapped[str | None] = mapped_column(Text, nullable=True)


class Checkpoint(Base):
    """Bookkeeping for the UI only. The chain's own integrity lives in git, as
    ``refs/kith/checkpoint`` and each commit's own parent link — see
    ``kith.infra.workspace.checkpoints._take_checkpoint``. Losing this table would only cost the UI's
    ability to list/correlate checkpoints; git would still have every one of them."""

    __tablename__ = "checkpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_root: Mapped[str] = mapped_column(Text, nullable=False)
    sha: Mapped[str] = mapped_column(Text, nullable=False)
    tree_sha: Mapped[str] = mapped_column(Text, nullable=False)
    parent_sha: Mapped[str | None] = mapped_column(Text, nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class FileTouch(Base):
    """One file a conversation has opened or changed, and the version of it he saw.

    Unique on ``(conversation_id, path)`` — the latest touch replaces the earlier one,
    because the question asked of this table is what state he believes a file to be in,
    not how many times he looked. ``version`` is ``size:mtime_ns`` at the moment of the
    touch, or ``''`` when the file was not there. ``extent`` is the window he saw
    (``"1-400 of 3000"``), or ``''`` when he has the whole file.
    """

    __tablename__ = "file_touches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False, default="")
    extent: Mapped[str] = mapped_column(Text, nullable=False, default="")
    at: Mapped[str] = mapped_column(Text, nullable=False)
