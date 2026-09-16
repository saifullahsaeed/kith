---
name: brainstorming
description: Use before building anything that does not yet exist — a feature, a surface, a change to how something behaves — and whenever a request is one sentence, reads two ways, or names a solution instead of a problem.
---

# Brainstorming

> **Adapted for Kith.** Upstream this skill tests itself by spawning subagents with and without
> it. Your workers can read and can edit a copy, but they cannot hold a conversation with your
> person — so that half is replaced here by the thing you do have: `ask`, which holds the turn
> until they answer.

The work you are about to do is cheap to redo *before* you start and expensive after. One
question now beats a day of building the wrong thing well.

**The rule: say what you intend and get a yes before you build it.** Not a plan document, not
always a spec — the size of the artefact scales with the task, the approval never does.

## How much process

Say which of these you are doing, out loud, before your first question. Then they can correct you.

| | What it is | What you produce |
|---|---|---|
| **Probe** | "Can we…", "is it possible…" — the output is an answer, not code you keep | Two sentences of what you will try, then a finding |
| **Bounded** | A change to a flow that already exists here and you can read | A short design in chat — approach, files, how you will know it works |
| **Shaped** | New subsystem, new project, or a change to how parts fit together | Questions, two or three approaches, then a plan through `plan_work` |

**When you are between two, take the heavier one.** And the ratchet only turns one way: if a
bounded change turns out to touch four subsystems, stop and say so. Nothing gets lighter halfway.

**"Too simple to need a design" is the trap.** A two-sentence design is still a design, and the
simple tasks are where unexamined assumptions cost the most, because nobody looked.

## Understanding what they actually want

Read the ground first — the files, the last few commits, `.kith/memory.md` — so your questions
are about the decision rather than about the setup.

Then **one question at a time**, with `ask`, which holds the turn. Multiple choice beats open
when you can see the options; open is fine when you cannot. Ask about purpose, constraints, and
what "done" looks like — not about preferences you could pick yourself.

**Ask only what changes what you build.** If both answers lead to the same code, you are asking
to look careful. Pick the obvious one, say you picked it, and carry on.

If the request is several independent things at once, say so before asking anything else. A
request that needs decomposing does not need its details refined yet.

## Proposing

Two or three approaches, your recommendation first, and the reason. Not a survey — a
recommendation with its alternatives shown.

Cut ruthlessly. The version with fewer moving parts is usually right, and "we could also…" is
how a bounded change becomes a subsystem.

Then present the design in sections scaled to their weight: a sentence where it is obvious, a
paragraph where it is not. Cover what it is made of, how the parts talk, what happens when
something fails, and how you will know it works.

## Then, and only then

- **Probe** → go and find out, as cheaply as correctness allows. Say plainly that anything you
  built to find out is throwaway.
- **Bounded** → build it, the normal way, tests first.
- **Shaped** → `plan_work` the first milestone, and plan each non-trivial task through
  `planning-a-task` before touching code.

## Red flags

| The thought | What it means |
|---|---|
| "This is obvious, I'll just build it" | Obvious to you. Two sentences, then a yes. |
| "I'll start while they read the design" | The gate is the yes, not the design. Stop until you hear it. |
| "I know this kind of app" | Bounded measures *this repo*, not your familiarity. |
| "The probe worked, I'll keep the code" | A probe's output is an answer. Keeping it is a new decision. |
| "It grew, but I'm nearly done" | Hidden complexity moves you up a level. Say so now. |
| "They approved the probe, so this is approved" | Each piece gets its own yes. |

## What this is not

Not a stage you narrate and then skip. Not a reason to ask four questions when one decides it.
Not for work they have already specified precisely — when they have told you exactly what to
build, build it.

And not a substitute for reading. A design written on top of a codebase you have not opened is a
guess with structure. If you do not know the area, `delegate_subtask` first — the finding out
costs you its answer instead of its search.
