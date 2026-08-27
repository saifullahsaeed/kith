"""The notification channel is scoped to what the conversation is about.

Same bleed as memory, on the channel. `messages_block` injected the last eight messages every
turn whatever the chat was about, so a brand-new chat about anything opened with a security
engagement's reach-outs — "I need an answer: the June test accounts are dead", "Finished Wave
3C". A message already knows where it came from: its `link` is `/chat/<id>` or `/tasks/<id>`,
which resolve to a project. `add_message` stamps that; the block shows a project's own channel
plus the global notes, and the rest stays in the inbox, which still shows everything.
"""

from __future__ import annotations

import pytest

from kith.infra.db import repositories as repo
from kith.services import conversations, memory_context


@pytest.fixture
def channel(db):
    """A task under a project, a conversation under another, and a linkless note — so a
    message can be stamped from each kind of link, and one stays global."""
    p_sec = repo.projects.add_project(db, "Security", "")
    p_erp = repo.projects.add_project(db, "ERP", "")
    task = repo.tasks.add_task(db, goal="wave 3a", project_id=p_sec["id"], status="working")
    convo = conversations.start(db, "erp chat")
    repo.conversations.set_project(db, convo["id"], p_erp["id"])
    return db, p_sec["id"], p_erp["id"], task["id"], convo["id"]


class TestAMessageIsStampedFromItsLink:
    def test_a_task_link_gives_the_task_s_project(self, channel):
        db, sec, _erp, task_id, _ = channel
        made = repo.messages.add_message(db, "Finished it", link=f"/tasks/{task_id}", kind="delivered")
        assert made["project_id"] == sec

    def test_a_chat_link_gives_the_conversation_s_project(self, channel):
        db, _sec, erp, _task, cid = channel
        made = repo.messages.add_message(db, "I need an answer", link=f"/chat/{cid}", kind="asked")
        assert made["project_id"] == erp

    def test_a_linkless_note_is_global(self, channel):
        db, *_ = channel
        made = repo.messages.add_message(db, "just a thought")
        assert made["project_id"] is None

    def test_a_dangling_link_does_not_raise_and_stays_global(self, channel):
        db, *_ = channel
        made = repo.messages.add_message(db, "orphan", link="/tasks/999999", kind="delivered")
        assert made["project_id"] is None


class TestWhatTheBlockShows:
    def _msgs(self, channel):
        db, sec, erp, task_id, cid = channel
        repo.messages.add_message(db, "SEC: found an IDOR", link=f"/tasks/{task_id}", kind="delivered")
        repo.messages.add_message(db, "ERP: odoo is slow", link=f"/chat/{cid}", kind="asked")
        repo.messages.add_message(db, "GLOBAL: two github accounts", kind="note")
        return db, sec, erp

    def test_a_project_chat_gets_its_own_and_the_global(self, channel):
        db, sec, _erp = self._msgs(channel)
        block = memory_context.messages_block(db, project_id=sec)
        assert "found an IDOR" in block
        assert "two github accounts" in block

    def test_a_project_chat_does_not_get_another_project_s(self, channel):
        db, sec, _erp = self._msgs(channel)
        block = memory_context.messages_block(db, project_id=sec)
        assert "odoo is slow" not in block

    def test_a_brand_new_unbound_chat_gets_only_the_global(self, channel):
        db, _sec, _erp = self._msgs(channel)
        block = memory_context.messages_block(db, project_id=None)
        assert "two github accounts" in block
        assert "found an IDOR" not in block
        assert "odoo is slow" not in block


class TestTheInboxStillSeesEverything:
    """Scoping is for the prompt, not the inbox. The default reader is unscoped, so the API and
    the inbox panel show the whole channel exactly as before — nothing is hidden, only un-dumped."""

    def test_the_default_list_is_not_scoped(self, channel):
        db, _sec, _erp, task_id, cid = channel
        repo.messages.add_message(db, "SEC msg", link=f"/tasks/{task_id}", kind="delivered")
        repo.messages.add_message(db, "ERP msg", link=f"/chat/{cid}", kind="asked")
        bodies = [m["body"] for m in repo.messages.list_messages(db)]
        assert "SEC msg" in bodies and "ERP msg" in bodies
