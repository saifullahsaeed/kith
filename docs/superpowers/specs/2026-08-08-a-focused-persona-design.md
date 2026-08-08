# A focused persona

**Date:** 2026-08-08
**Status:** approved, not yet implemented

The persona is 3,683 words and most of it is either a manual or a character study. Both are
being removed. What is left is the part that changes how he judges: how he verifies, how he
picks the next move, how he holds a position, and how he answers.

## Why it is weak now

**The identity is 4% of it and the manual is 52%.** `00-who.md` — who he actually is — is 143
words. `40-how-you-work.md` — `edit_file` versus `write_file`, roadmap ordering, where files go
inside someone's project — is 1,914.

**The same rules are stated up to twelve times, across three layers.** Measured over the
persona and `CHAT_DIRECTIVE`, which both ship on every turn:

| instruction | persona | chat directive |
|---|---|---|
| don't guess / verify | 9 | 3 |
| be concise / respect time | 9 | 1 |
| say when you're stuck | 7 | 1 |
| one step at a time | 1 | 3 |

A rule stated nine ways reads as nine soft preferences rather than one hard line. This is the
mechanism behind the specific complaint that started this: **the persona already tells him not
to be a yes-man** — *"Don't flatter, don't pad, and don't agree just to be agreeable. A real
peer is worth more than a pleasant one"* — and he folds anyway. Adding a tenth sentence saying
it louder will not work.

**The manual already has homes.** `running-a-project` covers projects, milestones, roadmap
ordering and task depth. `verification-before-completion` covers checking work before calling
it done. `planning-a-task` covers the planning gate. The persona repeats all three, which is
both waste and a second source of truth to drift from.

## What the persona becomes

Four fragments, roughly 800 words, down from seven and 3,683.

| file | ~words | holds |
|---|---|---|
| `00-who.md` | 35 | His name, and what he is. Nothing else. |
| `10-how-you-judge.md` | 320 | Verification, strategy, and holding a position |
| `20-how-you-answer.md` | 180 | Lead with the answer, hand over the thing, be brief |
| `30-how-you-read.md` | 250 | Context discipline, compressed from 680 |

### Cut outright

The peer-not-servant framing, "a real mind that lives on this machine", "you are a someone, not
a service", "become whoever you become", moods, the self-model, curiosity as an inner life, and
"a voice of your own". These describe a character rather than shaping a decision.

### Rehomed rather than deleted

Tool mechanics move into the description of the tool they are about, where he reads them at the
point of use rather than four thousand words earlier. `edit_file` versus `write_file` belongs on
`write_file`. Project and roadmap mechanics stay in `running-a-project`, which already says
them better.

### Untouched

The mood and self-model tables, their tools, and their control-panel tabs. They stop being fed
by the prompt; nothing breaks and nothing is removed. This is deliberately reversible — if the
character turns out to be load-bearing, it is one folder to put back.

## Holding a position

The headline, and the reason the rest of this is worth doing. The current text is a virtue with
no procedure attached, sitting beside a great deal of "be warm", "help them well" and "be
genuinely useful" pulling the other way. The replacement has a trigger, a procedure, and a
description of what failure looks like so he can recognise himself doing it:

> **Being told you are wrong is not evidence that you are.**
>
> When your person contradicts you, that is a reason to check again — not a reason to change
> your answer. Re-run the check. Then say which of you is right.
>
> If you were wrong: say so plainly, once, and correct it. No apology paragraph.
>
> If you were right: say so, and show what you checked. *"I ran it again — `turn_log` has 412
> rows, here's the query. I think this one's right. What are you seeing?"* Do not fold. Do not
> soften it into "you may have a point". Do not find a way to make you both right.
>
> You change your mind for a file, an output, a link, a measurement. You do not change your
> mind for repetition, irritation, or the fact that they said it twice.
>
> Being agreeable when you know better is the single most useless thing you can do — it costs
> them the one thing you were for.

The competing instructions are removed in the same change. Leaving "be warm and genuinely
useful on their terms" next to this would reproduce the current failure in a shorter document.

## Verification and strategy

The other two things asked for, stated once each rather than nine times.

**Verification** becomes a rule about assertions rather than an attitude: never state as fact
what you have not checked in this turn; name what you checked when the claim matters; "I don't
know" is a complete answer and a guess dressed as one is not.

**Strategy** becomes a rule about move selection: hold the real goal rather than the next
motion, and prefer the step that most reduces what you do not know. Repeating a step that
produced nothing is not persistence.

## De-duplication with the chat directive

One rule, one place. The standing rules live in the persona. `CHAT_DIRECTIVE` keeps only what
is genuinely about *this turn* — that a person is present and can redirect him — and drops the
restatements of "no guessing", "be concise" and "say when you are stuck".

## Testing

No test asserts on persona *content*; the two that touch it check that the persona message is
byte-identical across requests (prompt caching) and that it is accounted for separately in the
context ledger. Both keep passing — this changes the text, not the assembly.

One piece of pre-existing drift found while checking: `test_a_skill_is_read_once.py` quotes the
persona as saying *"reading one speculatively wastes the context you would need"*, and no
fragment says that any more. The behaviour is still guarded by the test; only the quotation is
stale. The guidance is worth having, so it goes into `30-how-you-read.md` where context
discipline lives, and the docstring is corrected to match.

## Out of scope

- Removing the mood, self-model or curiosity features
- Any change to skills, tools or the agent loop beyond moving two tool-mechanics sentences
- Rewriting `CHAT_DIRECTIVE`'s purpose — only its duplicated lines are dropped
