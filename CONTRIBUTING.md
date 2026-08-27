# Contributing to Kith

Kith is an agent that runs on your own machine, with your files and a real shell. That single
fact sets most of what follows: a bug here does not return a wrong answer, it deletes something.

## Getting it running

```bash
make venv          # server virtualenv
make ui            # build the interface
make server        # backend + UI on http://127.0.0.1:8611
```

`make help` lists the rest. Nothing here needs Docker: Kith works in a real folder on your
machine with the tools you already have, and `infra/permissions.py` is what bounds him.

## Before you open a pull request

```bash
./check            # everything CI runs
./check server     # python only, the fast loop
./check ui         # typescript only
```

Run the script rather than the individual commands. It exists because
`pytest | tail -1 && ruff` reports the exit status of `tail`, so a red suite scrolls past as one
line of green — that happened twice in one night and a commit went out on top of it.

The server side runs ruff, ruff format, pyright and pytest. The type check is a gate and sits at
zero — [`server/docs/typing.md`](server/docs/typing.md) covers what it checks, the one rule set
relaxed inside `tests/`, and the helpers that keep it there.

## House style

These are not arbitrary. Each one is here because its absence cost something.

**Comments say why, not what.** The prose-to-code ratio in this repo is about 0.5:1 and that is
deliberate. When you fix something subtle, write down the incident: what was observed, what the
measurement was, why the obvious fix is wrong. `services/agent_loop.py` and `llm/caching.py` are
the reference for the register.

**Measure, don't estimate.** Anywhere a number can come off the wire instead of out of your
head, take it off the wire. `llm/budget.py` calibrates tokens-per-character from what the
provider actually billed rather than shipping a tokenizer; `llm/openai_compat.py` reads
`usage.cost` rather than multiplying tokens by a rate card. If you add a threshold, say in a
comment what you measured to pick it.

**Tests are named after the behaviour, not the function.** `test_a_dropped_round_does_not_lose_the_turn.py`,
not `test_agent_loop.py`. The docstring should describe the real failure it prevents, with the
numbers. A test whose name only makes sense if you already know the code cannot tell a future
reader what broke.

**Prove the test fails first.** Especially for a regression: temporarily restore the old
behaviour and watch it go red. A test that passes against both versions is documentation, not a
guard.

**Never widen the permission surface quietly.** `infra/permissions.py` is the only thing
standing between the model and the disk. New shell-adjacent capability needs a matching gate and
a test, and if you leave a known gap, name it in a comment — as the module already does for
reads through the shell.

**Do not commit anything from `server/data/`.** It holds the API key in plaintext and his entire
memory. `.gitignore` was written before `git init` for this reason; keep it that way.

## Anything that talks to the model

Two rules that have each been broken and cost real money:

- **The prompt prefix is append-only during a turn.** Rewriting an earlier message invalidates
  the provider's cache from that point on, every round thereafter. If you need to shrink the
  history, do it the way `services/compaction.py` does — once, at a threshold, producing a new
  stable prefix — not a little more on every round.
- **A directive must not name a tool the same code path removes.** This has shipped three times
  (`edit_file`, `add_task`, `ask`). `tests/test_landing_can_finish_the_work.py` now guards the
  general form; if you add a directive, make sure it is covered.

## Reporting a bug

The transcripts under `server/data/conversations/` are plain JSONL on purpose — greppable, and
still readable in ten years. A bug report with the turn's context ledger (`built_in_tools`,
`retried`, `folded`) and its `turn_log` row is worth ten without.

Scrub it before you post it. Those files are your notes, your people, and your work.
