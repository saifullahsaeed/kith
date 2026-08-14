# Nested turns work today

`_run_turn` is re-entrant. A tool handler can run a whole second turn inside itself, with its
own toolset and its own round budget, and hand the answer back as its result. **No change to
the loop is required**, and none should be made on the strength of "subagents need it".

That was checked by running it, not by reading it. Against an unmodified `agent_loop.py`:

```
child ran:                        True
round 1 (parent):                 ['nest', 'web_search', 'write_file']
round 2 (child, narrowed):        ['web_search']
round 3 (parent again):           ['nest', 'web_search', 'write_file']
parent block identical round 1/3: True      # the prompt cache survives
child events leaked to parent:    0
child's answer captured:          "the child's actual answer"
```

It works because every piece of turn state is a per-call local — `call_index`, `seen_calls`,
`room`, `landing`, `retries` — so a nested call gets its own thrash guard, its own context
budget, its own landing latch and its own retry tally. `session_context.a_turn()` nests
properly too: the child gets an empty scratch and the parent's is restored on exit, so a skill
the parent read once is read again by the child rather than silently skipped.

The answer comes back by draining the child's generator and keeping the text deltas:

```python
said = "".join(
    event.get("text", "")
    for event in agent_loop._run_turn(messages, config, host, db, narrow_host, max_rounds=30)
    if event.get("type") == "delta" and event.get("role") == "text"
)
```

## Three rules for the tool that does it

These are not blockers. They are the contract, and each one fails quietly if broken.

**Never pass the parent's `conversation_id`.** `_run_turn` calls `offload.clear(conversation_id)`
during setup, which is `shutil.rmtree` on that conversation's spill directory. The parent's
stubs quote spilled paths that are already in its sent, cached history — deleting them leaves
the model a `read_file` that 404s and a prefix that cannot be corrected. Pass `""`; a nested
turn belongs to no conversation.

**Never put a delegating tool in `_PARALLEL_SAFE`.** The `ThreadPoolExecutor` at the bottom of
the round loop has no `copy_context`, so pool threads carry no context variables.
`session_context.current()` would be `""` inside the child and its file tools would resolve
against the wrong workspace root. The bar for that frozenset is already "network-bound, free of
side effects, indifferent to the others" — a nested turn is none of those.

**Know where the child's spend lands.** `_record` writes to a process-global tally, so the
child's tokens are counted, but the *parent's* per-turn stats do not attribute them — the
parent sees one tool call that took a while. If a delegate tool should report its own cost,
it has to carry that up itself.

## Why the loop is still worth decomposing, eventually

Not for this. `_run_turn` is 340 lines at cyclomatic complexity 40 and it is the highest-stakes
function in the system; splitting it is worth doing for legibility and for the next person who
has to change it. But it buys nothing that re-entrancy does not already give, and three
independent designs for it were reviewed here — two of them introduced silent behaviour changes
(a shared `_Retries` inflating the parent's retry count, a shared thrash guard blocking the
child's first repeat, a `nested()` helper that skipped `a_turn()`). A decomposition that looks
clean and changes behaviour is worse than the long function.

If it is done, the order that survived review is: make `_final_answer` return its text; move
the leaf helpers with no shared state (`measured`/`_record`/`usage_snapshot`, then the history
mutators); replace the `take_reading` closure with a hoisted partial; and stop before the
changes that cost signature churn across 19 call sites.
