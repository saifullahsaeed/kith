# Agent sessions — agents are conversations, arranged in a tree

*2026-10-20*

## Why

Every sub-agent Kith has had was a stripped-down loop living inside one tool call of the parent:
`delegate_subtask`, then builders, then scouts/builders/verifiers driven by workflow scripts and the
`add_agents` stepper. Each was given a narrowed tool list, a brief instead of the persona's working
context, a round cap, no way to ask anything, and a transcript only as a JSON file in a run folder.
The record of that design is in the earlier specs and audits: builders that read for thirty rounds
and wrote nothing (1 of 19 on Oct 2–3), scouts told to "run it" with no shell, verifiers checking an
unchanged tree, work lost when a run closed its copies, and a whole layer of machinery (script
sandbox, stepper ledger, resume-by-hash, patch carry, merge/revise kinds) built to make up for
agents that could not do the work themselves.

The person's call (2026-10-20): no stripped-down agents. An agent is a full chat session — same
persona, same tools, skills, plugins, MCP, browser, settings. The only thing that differs is who it
talks to.

## What the person decided

1. **Agents are full sessions.** Everything a chat can do, an agent can do. No withheld tools.
2. **One line of communication per agent.** The person talks to the main chat — the *conductor*.
   The conductor talks to its agents; an agent talks only to its *handler* (the conversation that
   started it) and to its own agents. Questions go up the chain one link at a time; instructions go
   down. Depth is unlimited and agents decide for themselves whether to start agents.
3. **Long-lived, linked transcripts.** Every agent is a real conversation with its own transcript,
   linked to its handler. If Kith dies, an agent picks up where it left off when Kith comes back.
4. **Steerable.** The conductor can steer, redirect, or re-task its agents; an agent can do the same
   to its own. A finished agent can be given more work.
5. **Strategy lives in a skill.** How many agents, how to split work, when to do it yourself — all of
   it comes from a skill, so strategies can be swapped by editing or choosing a skill. First
   strategy: divide and conquer. "If needed" applies at every level, including the conductor.
6. **Git worktrees, merged upward, cleaned up at the end.** Each agent works on its own branch in its
   own worktree. A handler merges its agents' work; the conductor merges everything into a running
   system. Worktrees are not deleted per run — the conductor cleans up when the task is complete.

## Shape

### An agent is a conversation row with a parent

`conversations` gains (migration v53): `parent_id`, `agent_name`, `agent_state`, `agent_model`,
`agent_worktree`, `agent_branch`, `agent_base`, `agent_source`. A conversation with no `parent_id` is
an ordinary chat; any chat becomes a conductor the moment it starts an agent.

`agent_state` is the persisted lifecycle — `working`, `waiting` (on its own agents), `done`
(reported, idle, can be re-tasked), `stopped`, `closed`. What is live right now (a turn running, a
question open) is still read from memory as before; the column is what survives a restart.

Because each agent has its own conversation id, everything already keyed by conversation is
automatically its own: live turn, stop switch, steer queue, browser, process names, plugin state,
the file ledger, spill directory, host pin. (Workers shared the parent's id and clobbered all of
these.)

### Mail is the only channel between sessions

A new `agent_mail` table: `to_id`, `from_id`, `kind`, `body`, `created_at`, `delivered_at`. Kinds:
`task`, `message` (down); `report`, `question`, `note` (up). Persisted, so nothing in flight is lost
to a restart.

Delivery (`services/agents.deliver`): if the recipient has a turn running, the mail is taken at its
next round boundary — the same hook steering uses. If it is idle, a turn starts with the mail as its
opening message. A per-conversation lock makes "is a turn running / start one" atomic, and the end of
every turn checks for mail that arrived after its last round.

In the recipient's transcript, mail from its handler is recorded as `user` (the handler is that
agent's person) and mail from its own agents as `system` (a wake, like a finished background task),
each with a `from` field naming the sender so the interface can label it.

### Reports

When an agent's turn ends and nothing below it is still busy, its final message is mailed to its
handler as a `report`, together with its branch status (commits ahead of where it started; any
uncommitted work is committed first so nothing is stranded in a worktree). If its own agents are
still busy it does not report — it is `waiting`, and their reports wake it. So a handler receives one
report per assignment, from a subtree that has finished.

### Asking

In an agent, `ask` goes to the handler: the question is mailed up and the tool call blocks until the
handler answers with `message_agents` (or the agent is stopped). The handler may answer from what it
knows, or `ask` its own handler — up to the conductor, whose `ask` reaches the person. `reach_out`
from an agent is mailed up as a `note` rather than alerting the person.

### Tools (everyone has them)

- `start_agents(agents=[{name, task, model?, workspace?}])` — plural on purpose. `workspace` is
  `own` (default inside a git repo: a worktree on a new branch from the caller's HEAD) or `shared`
  (work in the caller's folder — for research that changes nothing). `model` is `main`, `strong`,
  `fast` or a model id.
- `message_agents(messages=[{agent, text}])` — answers an open question, steers a running agent, or
  re-tasks an idle one. One verb for everything said downward.
- `stop_agents(agents=[...])` — stops their turns and everything under them. They stay resumable.
- `close_agents(agents=[...] | ["all"], force?)` — the cleanup: removes worktrees and branches,
  refusing branches not merged into the caller's HEAD unless forced. Closes the whole subtree.

Names are relative to the caller: you can only address your own agents.

### Prompt

`config.system` (the cached head, identical for every session) becomes persona + skill index + the
active **strategy skill's body** (`agent_strategy` setting, default `divide-and-conquer`). After it,
an ordinary chat gets `CHAT_DIRECTIVE`; an agent gets an agent directive naming its handler, its
chain, and its worktree/branch. Any session with agents gets a `[Your agents]` block in its
present state: name, state, branch, last line.

### Worktrees

`git worktree add -b kith/<slug>-<id> <data>/agents/<conversation-id> HEAD`, run in the caller's
working folder — so an agent's agents branch from the agent's own branch. Ignored notes (`.kith`),
`.env*` files and dependency folders are carried as before. The agent's turns are pinned to the
worktree (relative paths, shell cwd, absolute paths into the original repo bent into the copy) but
**not sandboxed**: full freedom, as decided. Copies are removed only by `close_agents`, guarded to
paths inside Kith's own agents folder.

### Restart

At boot: every agent whose state was `working` gets a note ("Kith restarted while you were working;
carry on from where your transcript ends") and a turn; all undelivered mail is delivered. The
conductor wakes when its agents report.

### Interface

Agents do not crowd the chat list: they nest under their conductor, indented by depth, with a state
dot. An agent's conversation opens read-only — a banner names its handler, offers Stop, and links up
the chain — and messages are labelled with who sent them. The workflow agent map, run board and
Workflows chip are replaced by an agent tree.

## Removed

The workflow system, entire: `run_workflow`, `check_workflow`, `stop_workflow`, `add_agents`,
`submit`, the role tables and briefs (`tools/workers.py`), the script helper and saved workflows
(`server/workflow/`), the run board, the `/workflow` prefix and workflow mode, the copy sandbox for
agent commands, the workflow persona/skill text, their routes, UI and tests. The `workers` table and
`workflow_mode` column stay in the schema, unwritten.

## Calls made without asking

- Person-Stop on the conductor stops only the conductor's turn; agents carry on (as Claude Code's
  background agents do). `stop_agents`, and the tree view, stop agents.
- Agents' permission prompts behave as in any chat (in bypass mode there are none).
- No depth or count limit in code. Cost is shown per tree; the strategy skill is where restraint
  lives.
- Agents branch from the caller's last commit. Uncommitted changes are not carried — `start_agents`
  says so when there are any, and the strategy tells handlers to commit before delegating.
- The conductor merges into whatever branch the person's checkout is on, with ordinary git.
