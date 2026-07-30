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
    status: Mapped[str] = mapped_column(Text, nullable=False, default="todo")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(Text, nullable=False, default="normal")
    due_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
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
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fire_at: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    read: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
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


class Curiosity(Base):
    __tablename__ = "curiosities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="open")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


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


class TickLog(Base):
    """One row per autonomy tick — the durable flight recorder."""

    __tablename__ = "tick_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    focus: Mapped[str | None] = mapped_column(Text, nullable=True)
    tools: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
