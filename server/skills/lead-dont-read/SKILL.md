---
name: lead-dont-read
description: Agent strategy — whoever starts agents leads them and does not do their work. Read-only work is map, reduce, verify; building is contract, build, verify. One level deep unless a part is truly big. Read by every session as its strategy for using agents.
metadata:
  kind: agent-strategy
---

You can start agents with `start_agents`: full sessions like you, each with its own conversation and every tool you have. Every agent you start follows this same strategy for its own part.

**1. Do it, or lead it — never both on the same code.** Small or tightly coupled work — one change, a question, a fix whose parts touch the same files — do it yourself. Work that splits into parts that can move forward independently: lead it. Once you lead, you do not read or change what you handed out. Your context stays small so your judgement stays sharp; theirs holds the detail.

**2. Look before you split.** Learn the shape of the work: the structure, the folder's CLAUDE.md / AGENTS.md, any spec, how the tests run. If something only your person or your handler can answer would change the split, `ask` now — before anyone works on a guess.

**3. Read-only work — audits, reviews, research: map, reduce, verify.**
- *Map:* 4–6 agents with `workspace: "shared"`, one tight scope each (an app, a layer, a question). No worktrees — nobody is changing anything.
- Tell every one of them to report findings in exactly this shape, one per finding: **severity · `file:line` · what is wrong · the evidence (quote the lines) · how to confirm it**. Nothing else in the report but a one-line summary of what was covered and what was not.
- *Reduce:* merge duplicates, rank by severity.
- *Verify:* for each high or critical finding, start one fresh agent with `model: "strong"` and `workspace: "shared"` whose only job is to prove that finding wrong — give it the finding and nothing else. Report what survives, and say which ones did not.

**4. Building — features, fixes across modules, refactors: contract, build, verify.**
- *Contract:* write the shared interfaces, data shapes and routes yourself, and commit them. Agents start from your last commit; anything uncommitted is invisible to them.
- *Build:* one builder per module, each on its own branch (the default workspace), each owning its files. Two agents editing one file is a merge conflict you scheduled.
- *Verify:* after merging, one agent with `model: "strong"` checks the result against the person's own words — not your brief — and runs it.
- Merge, fix or send back, run the whole thing.

**5. One level.** An agent splits its part again only when that part is far beyond one sitting — more than about ten files to change or three thousand lines to read. Otherwise it does the work. Every extra level is another coordinator with its own growing context and another hop for every question.

**6. Write each task so it stands alone.** The agent cannot see your conversation. Say the goal and why, what exists (paths), the contract it must keep, what it owns and must not touch, how to check its work, what done means, and — for read-only work — the finding format above.

**7. While they work, wait.** Call `wait_for_agents`. It returns when one of them reports or asks you something. Answer questions from what you know; `ask` up only what you cannot. Steer with `message_agents` when a report or a question shows drift. Do not read their code while you wait — that is the one thing this strategy exists to stop.

**8. A report is a claim.** For building: read the branch (`git log -p HEAD..<branch>`), merge it, run it. For findings: the verify step is the check. Send short or wrong work back with `message_agents` — it keeps everything it learned.

**9. Report up what is true.** Your last message is your report: what was done, what you ran and what it showed, what is still open.

**10. Clean up once, at the end.** When everything is merged and working, `close_agents(["all"])`. Not between steps — a finished agent can still be given more work.
