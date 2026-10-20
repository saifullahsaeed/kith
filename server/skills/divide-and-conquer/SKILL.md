---
name: divide-and-conquer
description: Agent strategy — split a task into independent parts by where they live in the code, hand each to an agent that splits again only if its part does, then merge everything back into one working system. Read by every session as its strategy for using agents.
metadata:
  kind: agent-strategy
---

You can start agents with `start_agents`: full sessions like you, each with its own conversation, worktree and branch. Every one of them follows this same strategy for its own part — so the work becomes a tree, as deep as it needs to be and no deeper.

**1. Do it yourself unless it splits.** One change, one file, a question, a fix whose parts all touch the same code — do it. Agents earn their cost when the work has parts that can move forward independently, or a part that needs a lot of reading you do not need to carry. This applies at every level: an agent whose part is small just does it.

**2. Look before you split.** Find out what exists: the structure, the conventions (the folder's CLAUDE.md / AGENTS.md), any design or spec, the tests and how to run them. If something only your person (or your handler) can answer would change the split, `ask` now — before anyone starts work on a guess.

**3. Split along the code, not the to-do list.** A part is a slice someone can own end to end: a module, a layer, a feature, a set of pages. Work that touches the same files belongs to one agent — two agents editing one file is a merge conflict you scheduled. Size parts so each can be finished and checked in one sitting. Usually two to five parts per level.

**4. Fix the seams first, then fan out.** Where parts meet — an interface, a data shape, a route, a shared type — decide it, write it, and commit it before starting anyone, so every branch starts from it. Agents start from your last commit: anything uncommitted is invisible to them.

**5. Write each task so it stands alone.** The agent cannot see your conversation. Say the goal and why, what exists (paths), the contract it must keep, what it owns and must not touch, how to check its work, and what done means. Name it for its part.

**6. While they work, step back.** End your turn; reports arrive on their own and wake you, and so do their questions. Answer a question from what you know, or `ask` up if you cannot. Steer with `message_agents` if you see one going wrong.

**7. A report is a claim — check it.** Read the branch (`git log -p HEAD..<branch>`), run what it says it ran. If it is wrong or short, send it back with `message_agents`: it keeps everything it learned. Then `git merge <branch>` into your checkout, resolve conflicts, and run the whole thing — tests, build, the app. A part that works alone and breaks the whole is not done. Missing piece? Do it, or start an agent for it.

**8. Report up what is true.** Your report is your last message: what was built, what you merged, what you ran and what it showed, what is still open. Nothing is "done" that you did not run.

**9. Clean up once, at the very end.** When the whole task is merged and working, `close_agents(["all"])` removes the worktrees and branches. Not between rounds — a finished agent can still be given more work, and closing it throws away what it knows.
