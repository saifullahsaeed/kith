"""Schema history. Migration at index *i* upgrades version *i* to *i+1*, so
changing the schema means appending a function — never editing an old one."""

from __future__ import annotations

from pathlib import Path

from kith.infra.db.connection import apply_migrations, connect
from kith.infra.db.support import utc_now_iso


def _migrations():
    def v1_brain(conn):
        conn.executescript(
            """
            CREATE TABLE memories (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                content    TEXT NOT NULL,
                tags       TEXT NOT NULL DEFAULT '[]',   -- JSON array of strings
                importance INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE notes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                title      TEXT NOT NULL,
                body       TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE journal (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                entry      TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE tasks (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                goal       TEXT NOT NULL,
                status     TEXT NOT NULL DEFAULT 'open',  -- open | doing | done | dropped
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )

    def v2_custom_tools(conn):
        conn.execute(
            """
            CREATE TABLE custom_tools (
                name        TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                parameters  TEXT NOT NULL DEFAULT '{}',   -- JSON schema properties
                required    TEXT NOT NULL DEFAULT '[]',    -- JSON array of names
                language    TEXT NOT NULL DEFAULT 'python',-- python | bash
                code        TEXT NOT NULL,
                created_at  TEXT NOT NULL
            )
            """
        )

    def v3_memory_levels(conn):
        # How present a memory is: 'core' = always in mind; 'recall' = kept, and
        # retrieved when reached for. (Recent memories surface on their own.)
        conn.execute("ALTER TABLE memories ADD COLUMN level TEXT NOT NULL DEFAULT 'recall'")

    def v4_memory_embeddings(conn):
        # A packed float32 vector of the memory's content, so recall can match by
        # meaning (cosine similarity) rather than shared words. NULL until embedded.
        conn.execute("ALTER TABLE memories ADD COLUMN embedding BLOB")

    def v5_reminders(conn):
        # Notes to his future self, tied to a moment. The scheduler surfaces
        # them when they come due. Times are stored UTC ISO 8601.
        conn.execute(
            """
            CREATE TABLE reminders (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                fire_at    TEXT NOT NULL,
                note       TEXT NOT NULL,
                status     TEXT NOT NULL DEFAULT 'pending',  -- pending | done | cancelled
                created_at TEXT NOT NULL
            )
            """
        )

    def v6_messages(conn):
        # Things Kith chooses to say to his person on his own — his side of the
        # conversation when no one asked. Surfaced in the UI; 'read' tracks whether
        # they've seen it.
        conn.execute(
            """
            CREATE TABLE messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                body       TEXT NOT NULL,
                read       INTEGER NOT NULL DEFAULT 0,  -- 0 unread, 1 read
                created_at TEXT NOT NULL
            )
            """
        )

    def v7_mood(conn):
        # His felt inner state — a single evolving row. He sets it himself; it
        # colours his tone and the room. energy is 0..100.
        conn.execute(
            """
            CREATE TABLE mood (
                id         INTEGER PRIMARY KEY CHECK (id = 1),
                label      TEXT NOT NULL DEFAULT 'settling in',
                energy     INTEGER NOT NULL DEFAULT 60,
                note       TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO mood (id, label, energy, note, updated_at) VALUES (1, 'settling in', 60, '', ?)",
            (utc_now_iso(),),
        )

    def v8_people(conn):
        # The people Kith knows — chiefly his person. He builds each profile over
        # time so he isn't re-learning the same facts about them.
        conn.execute(
            """
            CREATE TABLE people (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT NOT NULL UNIQUE COLLATE NOCASE,
                relationship TEXT NOT NULL DEFAULT '',
                profile      TEXT NOT NULL DEFAULT '',
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            )
            """
        )

    def v9_curiosities(conn):
        # Things Kith is curious about — the seeds of a life of his own, beyond
        # whatever task is in front of him.
        conn.execute(
            """
            CREATE TABLE curiosities (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                topic      TEXT NOT NULL,
                note       TEXT NOT NULL DEFAULT '',
                status     TEXT NOT NULL DEFAULT 'open',  -- open | exploring | explored | dropped
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    def v10_self(conn):
        # Who Kith understands himself to be — a single evolving self-model he
        # shapes over time. 'identity' is a short line; 'profile' is his values,
        # views, and sense of himself, grown as he lives.
        conn.execute(
            """
            CREATE TABLE self (
                id         INTEGER PRIMARY KEY CHECK (id = 1),
                identity   TEXT NOT NULL DEFAULT '',
                profile    TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO self (id, identity, profile, updated_at) VALUES (1, '', '', ?)", (utc_now_iso(),)
        )

    def v11_sources(conn):
        # Knowledge his person feeds him — links and documents he reads and can
        # recall by meaning later. Embedded like memories.
        conn.execute(
            """
            CREATE TABLE sources (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                title      TEXT NOT NULL,
                origin     TEXT NOT NULL DEFAULT '',   -- url, or 'pasted'
                content    TEXT NOT NULL,
                created_at TEXT NOT NULL,
                embedding  BLOB
            )
            """
        )

    def v13_message_sender(conn):
        # Messages become two-way: 'kith' (his reach-outs) or 'user' (your replies).
        conn.execute("ALTER TABLE messages ADD COLUMN sender TEXT NOT NULL DEFAULT 'kith'")

    def v14_tasks_as_issues(conn):
        # Tasks grow into real issues: priority, a due date, a description / definition
        # of done, provenance, and Kanban columns.
        conn.execute("ALTER TABLE tasks ADD COLUMN priority TEXT NOT NULL DEFAULT 'normal'")
        conn.execute("ALTER TABLE tasks ADD COLUMN due_at TEXT")
        conn.execute("ALTER TABLE tasks ADD COLUMN description TEXT NOT NULL DEFAULT ''")
        conn.execute("ALTER TABLE tasks ADD COLUMN created_by TEXT NOT NULL DEFAULT 'kith'")
        # Old vocabulary (open) -> new columns (todo). doing/done/dropped carry over.
        conn.execute("UPDATE tasks SET status = 'todo' WHERE status = 'open'")

    def v15_projects(conn):
        # Projects group tasks toward a bigger goal, with a roadmap of milestones on
        # a timeline. When a project is done he leaves its tasks alone and rests.
        conn.execute(
            """
            CREATE TABLE projects (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status      TEXT NOT NULL DEFAULT 'active',  -- active | done | paused | archived
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE milestones (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id  INTEGER NOT NULL,
                title       TEXT NOT NULL,
                target_at   TEXT,                            -- optional target, UTC ISO
                status      TEXT NOT NULL DEFAULT 'todo',    -- todo | done
                order_index INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            )
            """
        )
        conn.execute("ALTER TABLE tasks ADD COLUMN project_id INTEGER")

    def v16_task_detail(conn):
        # A task's detail: a comment thread (two-way: kith/user), a checklist of
        # sub-steps, and deliverables (the outputs the task produced).
        conn.execute(
            """
            CREATE TABLE task_comments (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id    INTEGER NOT NULL,
                author     TEXT NOT NULL,            -- 'kith' | 'user'
                body       TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE checklist_items (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id     INTEGER NOT NULL,
                text        TEXT NOT NULL,
                done        INTEGER NOT NULL DEFAULT 0,
                order_index INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE deliverables (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id    INTEGER NOT NULL,
                kind       TEXT NOT NULL DEFAULT 'text',  -- text | file | link
                title      TEXT NOT NULL,
                content    TEXT NOT NULL,                 -- the text, a sandbox path, or a url
                created_at TEXT NOT NULL
            )
            """
        )

    def v12_schedules(conn):
        # Standing jobs that fire on a cadence (unlike one-shot reminders) — a
        # daily briefing, a recurring check. Times stored UTC ISO.
        conn.execute(
            """
            CREATE TABLE schedules (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                note          TEXT NOT NULL,
                every_minutes INTEGER,       -- interval mode
                daily_at      TEXT,          -- 'HH:MM' local mode
                next_fire     TEXT NOT NULL,
                last_fired    TEXT,
                status        TEXT NOT NULL DEFAULT 'active',  -- active | paused
                created_at    TEXT NOT NULL
            )
            """
        )

    def v17_source_chunks(conn):
        # Real RAG: a source is split into chunks, each embedded, so long documents
        # are retrievable by the passage that matches, not just their title.
        conn.execute(
            """
            CREATE TABLE source_chunks (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                ord       INTEGER NOT NULL DEFAULT 0,
                text      TEXT NOT NULL,
                embedding BLOB
            )
            """
        )

    def v18_milestone_link_and_notify(conn):
        # Tasks can hang off a milestone (not just a project), so finishing tasks
        # can roll a milestone — and then a project — to done. Messages carry an
        # optional link so a notification can be clickable straight to its task.
        conn.execute("ALTER TABLE tasks ADD COLUMN milestone_id INTEGER")
        conn.execute("ALTER TABLE messages ADD COLUMN link TEXT")

    def v19_tick_log(conn):
        # A durable flight recorder: one row per turn, so you can judge
        # how he's doing over time (mode, what he focused on, tools used, tokens,
        # duration, outcome) even across restarts — the live Mind feed is ephemeral.
        conn.execute(
            """
            CREATE TABLE tick_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                at         TEXT NOT NULL,
                mode       TEXT NOT NULL,
                focus      TEXT,
                tools      TEXT,               -- JSON array of tool names used
                tokens_in  INTEGER NOT NULL DEFAULT 0,
                tokens_out INTEGER NOT NULL DEFAULT 0,
                seconds    REAL NOT NULL DEFAULT 0,
                outcome    TEXT
            )
            """
        )

    def v20_tick_tokens_uncached(conn):
        # tokens_in counts every token he was SHOWN, which re-counts a cached prefix on
        # every round of a tick — on a warm cache that is more than ten times the tokens
        # a provider actually had to read, so the flight recorder was reading like he
        # spends a fortune per step. This column is the honest figure. Nullable rather
        # than DEFAULT 0: rows written before this existed genuinely don't know, and 0
        # would be indistinguishable from a tick that was entirely cache hits.
        conn.execute("ALTER TABLE tick_log ADD COLUMN tokens_uncached INTEGER")

    def v21_conversations(conn):
        # Chat was ephemeral: the interface held the messages in React state and the
        # server held none, so a reload ended a conversation permanently. This indexes
        # them; the words live in append-only JSONL beside his work, because a plain-text
        # transcript outlives this program and a table does not.
        conn.execute(
            """
            CREATE TABLE conversations (
                id         TEXT PRIMARY KEY,
                title      TEXT NOT NULL,
                session_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                messages   INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute("CREATE INDEX idx_conversations_updated ON conversations (updated_at DESC)")

    def v22_milestone_graph(conn):
        # Milestones were decoration. Nothing consulted them when choosing what to work on
        # next — the runner sorted tasks by priority and the roadmap was a progress bar — so
        # a "roadmap" imposed no order on anything and there was no reason to keep one.
        #
        # Dependencies are what make them matter: a milestone whose predecessors are not
        # done is not available, and neither are its tasks. That turns the roadmap into the
        # thing that decides what happens next, which is what a roadmap is for.
        #
        # An edge table rather than a column, because this is a graph and the useful
        # questions ("what is ready", "what does this block") are joins.
        conn.execute(
            """
            CREATE TABLE milestone_deps (
                milestone_id  INTEGER NOT NULL,   -- waits for
                depends_on_id INTEGER NOT NULL,   -- this one
                PRIMARY KEY (milestone_id, depends_on_id)
            )
            """
        )
        conn.execute("CREATE INDEX idx_milestone_deps_on ON milestone_deps (depends_on_id)")
        # Where the node sits when someone has arranged the graph by hand. NULL means
        # "nobody has", which the interface lays out automatically — a stored 0,0 would be
        # indistinguishable from a deliberate top-left corner.
        conn.execute("ALTER TABLE milestones ADD COLUMN x REAL")
        conn.execute("ALTER TABLE milestones ADD COLUMN y REAL")

    def v23_message_kind(conn):
        # Everything he wrote reached you the same way: a note on a task, a question he was
        # blocked on, and "I'm stuck" all arrived as one undifferentiated stream, all of it
        # unread, all of it worth a notification. So the interesting ones were buried in the
        # routine ones, and the only way to stop being interrupted was to stop looking.
        #
        # The kind is what makes a threshold possible: keep every message as a record, and
        # let it decide which of them are allowed to interrupt you.
        conn.execute("ALTER TABLE messages ADD COLUMN kind TEXT NOT NULL DEFAULT 'note'")

    def v25_session_work(conn):
        # A conversation becomes the unit of work. Two columns carry it.
        #
        # `project_id`: what this session is working on. Project memory was being injected by
        # finding "the only active project with a folder", which is a guess that stops working
        # the moment you have two — and two at once is the whole point of sessions.
        #
        # `working`: whether he keeps going after finishing a step. This replaces roaming, and
        # the difference is that roaming was one global switch over one global board. "Should
        # he pick something up" is not one question with one answer once work belongs to
        # sessions; it is a property of each.
        conn.execute("ALTER TABLE conversations ADD COLUMN project_id INTEGER")
        conn.execute("ALTER TABLE conversations ADD COLUMN working INTEGER NOT NULL DEFAULT 0")

    def v24_project_directory(conn):
        # A project is work in a folder, and until now it was only rows. Nothing recorded
        # where the code actually was, so "which project is this" had to be inferred from
        # whatever he happened to be reading — and a project's own memory has nowhere to live
        # if the project does not know its own directory.
        #
        # Nullable, because a project that is not code (a shortlist, a piece of research) has
        # no directory and should not be given a pretend one.
        conn.execute("ALTER TABLE projects ADD COLUMN directory TEXT")

    def v26_checkpoints(conn):
        # A safety net under every file change he makes. See
        # kith.infra.workspace.checkpoints._take_checkpoint/_checkpoint_before_change — this table is
        # bookkeeping for the UI, not the source of truth for the chain itself, which lives
        # entirely in git as refs/kith/checkpoint and its own parent links.
        conn.executescript(
            """
            CREATE TABLE checkpoints (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_root       TEXT NOT NULL,
                sha             TEXT NOT NULL,
                tree_sha        TEXT NOT NULL,
                parent_sha      TEXT,
                conversation_id TEXT,
                trigger         TEXT NOT NULL,
                created_at      TEXT NOT NULL
            );
            CREATE INDEX idx_checkpoints_conversation ON checkpoints (conversation_id, created_at);
            CREATE INDEX idx_checkpoints_repo ON checkpoints (repo_root, created_at DESC);
            """
        )

    def v27_reminder_conversation(conn):
        # Which chat asked to be reminded, so firing one can report back to it instead of
        # whichever session the tick's round-robin happened to be on. Nullable: a reminder set
        # outside any conversation — none exist yet, but the column has to allow for one — has
        # nothing to report back to and keeps today's tick-wide behaviour.
        conn.execute("ALTER TABLE reminders ADD COLUMN conversation_id TEXT")
        conn.execute("ALTER TABLE schedules ADD COLUMN conversation_id TEXT")

    def v28_task_planning_statuses(conn):
        # A plan-then-implement gate, the same shape 'open' -> 'todo' took in v14: old
        # vocabulary out, new columns in, existing rows carried forward rather than left
        # speaking a word the app no longer recognises.
        #
        # 'todo' meant "actionable, not started" — under the gate nothing is actionable until
        # a plan for it has been approved, so there is no direct equivalent. Existing rows are
        # grandfathered into 'planned' rather than sent back to 'planning': the gate is for
        # work entered from here on, not a retroactive plan demanded of a queue that was never
        # asked to write one. 'doing' -> 'working' is a plain rename; 'backlog', 'review',
        # 'waiting', 'done' and 'dropped' are unchanged.
        conn.execute("UPDATE tasks SET status = 'planned' WHERE status = 'todo'")
        conn.execute("UPDATE tasks SET status = 'working' WHERE status = 'doing'")

    def v29_turn_log(conn):
        # `tick_log` was never about ticks. `add_tick_log(..., mode="chat", ...)` is called
        # from the chat route, so this table is the flight recorder for *turns*: a
        # conversation's cost lives here, and it is most of what the money dashboard reads.
        # The loop it was named after is gone. The rows are not, and this is a rename rather
        # than a rebuild precisely so that stays true — no copy, nothing to get wrong.
        #
        # `conversations.working` is dropped separately, in v30, and the order is not a
        # preference. Dropping a column the SQLAlchemy model still maps breaks every query
        # against that table at once, so the column outlives the code that reads it by exactly
        # one migration.
        conn.execute("ALTER TABLE tick_log RENAME TO turn_log")

    def v30_no_roaming(conn):
        # `working` meant "this session keeps going without being asked". Nothing keeps
        # going without being asked any more, so the column is not renamed into something
        # truer — there is no truer thing for it to say. Its sibling from v25,
        # `project_id`, stays: what a session is working on is read on every turn to pick
        # the project memory.
        conn.execute("ALTER TABLE conversations DROP COLUMN working")

    def v31_file_touches(conn):
        # Which files a conversation has opened or changed, and the version of each he saw.
        # One row per (conversation, path) rather than one per touch: the question asked of
        # this table is "what is the state of what he has seen", which only the latest touch
        # answers, and a history of every read would grow without bound on a long session.
        #
        # `version` is `size:mtime_ns` as of the touch, or '' when the file was not there.
        # Staleness is not stored — it is the comparison between this and the file now, so
        # a change made outside Kith's own tools is caught without anything having written
        # a row for it.
        conn.executescript(
            """
            CREATE TABLE file_touches (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                path            TEXT NOT NULL,
                action          TEXT NOT NULL,   -- read | wrote
                version         TEXT NOT NULL DEFAULT '',
                at              TEXT NOT NULL
            );
            CREATE UNIQUE INDEX file_touches_one_per_path
                ON file_touches (conversation_id, path);
            """
        )

    def v32_file_touch_extent(conn):
        # How much of the file he actually saw. `read_file` windows to 400 lines by default,
        # so "he has read this" was true of a 3,000-line file he had seen an eighth of — and
        # the manifest went on to tell him not to open it again. '' means the whole thing:
        # a short read that finished, or a write, where he supplied every byte himself.
        conn.execute("ALTER TABLE file_touches ADD COLUMN extent TEXT NOT NULL DEFAULT ''")

    def v33_task_conversation(conn):
        # Which conversation is working a task, so a session can say what it is on.
        #
        # Stamped when a task moves to `working` and cleared when it leaves — a task is worked
        # in one place at a time, and a stale link would put someone else's work at the top of
        # your chat. Nullable and unset for every task that already exists: none of them were
        # started in a conversation that knew to record it, and inventing a link would be worse
        # than having none.
        conn.execute("ALTER TABLE tasks ADD COLUMN conversation_id TEXT")

    return [
        v1_brain,
        v2_custom_tools,
        v3_memory_levels,
        v4_memory_embeddings,
        v5_reminders,
        v6_messages,
        v7_mood,
        v8_people,
        v9_curiosities,
        v10_self,
        v11_sources,
        v12_schedules,
        v13_message_sender,
        v14_tasks_as_issues,
        v15_projects,
        v16_task_detail,
        v17_source_chunks,
        v18_milestone_link_and_notify,
        v19_tick_log,
        v20_tick_tokens_uncached,
        v21_conversations,
        v22_milestone_graph,
        v23_message_kind,
        v24_project_directory,
        v25_session_work,
        v26_checkpoints,
        v27_reminder_conversation,
        v28_task_planning_statuses,
        v29_turn_log,
        v30_no_roaming,
        v31_file_touches,
        v32_file_touch_extent,
        v33_task_conversation,
    ]


def init(path: Path) -> None:
    """Create/upgrade the agent database."""
    conn = connect(path)
    try:
        apply_migrations(conn, _migrations())
    finally:
        conn.close()
