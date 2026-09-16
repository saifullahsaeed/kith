---
name: sending-workers
description: Use when a job splits into parts that touch different files — the same change at many call sites, several packages needing the same treatment, several areas to understand at once — or when you are about to walk a list of files one at a time.
---

# Sending workers

> **Adapted for Kith.** Upstream this is about subagents that fix failing tests. Yours cannot run
> anything, so the shape is different: a worker finds out or edits, and **you** run the tests.

You have two kinds of worker and one way to push on either.

| | What it does | What comes back |
|---|---|---|
| `delegate_subtask` | Reads, searches, browses in a scratchpad nobody sees | A few hundred words |
| `send_builder` | Edits files in its own copy of the repository | A patch **you** apply, plus a report |
| `follow_up` | Asks one it already has one more thing | Either, without starting over |

**The thing they save is context, not rounds.** Twelve files you open are in your window for the
rest of the turn and re-sent every round after it. Twelve files a worker opens cost you its
summary. Doing it yourself was never slow — it was expensive later.

## Is this worker-shaped

Ask one question: **do the parts touch different files?**

- Four packages needing the same treatment → yes, one worker each.
- The same rename at six call sites in six files → yes.
- Understanding three subsystems before you decide → yes, one scout each.
- A change where part two depends on what part one turns out to be → **no.** Do part one, then decide.
- Two workers that would both edit `app/core.py` → **no.** Two patches, and they will not both apply.

That last one is the only hard rule here. Separate copies are what make them safe to run
together, and two builders given the same file are two copies of a file that disagree.

## Send them in ONE round

Several calls in one round run in parallel. The same calls in separate rounds run one after
another and cost the same. This is the single largest difference between using this well and
badly, and it is decided by where you put the calls.

## Writing the brief

A worker knows **nothing** about your plan — only the paragraph you send. Everything you leave
out, it invents, confidently, in the house style.

So say: which files, what change, what "done" looks like. For a scout: what you already know,
where to look, what a useful answer contains.

Three ways a brief has actually wasted a whole worker:

- **An unverified hint.** A brief pointed a builder at the wrong function and claimed a constant
  existed that did not. It checked, refused, and said so — but a compliant worker would have
  written two false docstrings. **Verify the file, the function and the name before you send
  them,** not after the patch comes back.
- **Too much in one bite.** A builder given a whole package spent its entire round budget reading
  and produced no patch at all. Fewer files each, more workers.
- **A vague objective.** "Improve the error handling in `app/`" comes back as a confident patch
  built on a guess. `send_builder` is for changes you have already decided.

Notes you write under `.kith/` are carried into a worker's copy, so a convention or a contract
you put there is something you can point it at. Nothing else untracked is.

## When they come back

**Read the patch before you apply it.** It is the only evidence that survives — the prose says
what the worker meant to do, and those are not always the same thing.

**An empty patch means one of three things** and only its own report tells you which: nothing
needed changing, it ran out of rounds before it started writing, or it described an edit it never
made. Send it again at less of the work for the second; check the patch not the prose for the third.

**Run the tests yourself.** A worker cannot run anything, which is exactly why testing its work
is yours and not something to take on trust.

**A near-miss is a `follow_up`, not a new worker.** It still has its brief, its own last report,
and — for a builder — the copy it edited. What it does *not* still have is everything it read on
the way; that was thrown away, which is the point. So a follow-up needing the same ground covered
again is cheaper as a fresh errand with a sharper objective.

## What they cannot do

Neither can commit, publish, run a command, run the tests, file anything on the board, remember
anything, ask you a question, or send a worker of its own. If the job needs one of those, it
comes back to you — which is the design, not a gap.

## Common mistakes

- **Walking the list yourself** because each file looks quick. Four quick files is four files in
  your window for the rest of the turn.
- **One worker per round.** Same cost, none of the parallelism.
- **Sending one to decide what to do.** It has none of your context and cannot ask.
- **Trusting the report over the diff.** The diff is what happened.
- **Overlapping files.** Two patches, one tree, no way to apply both.
