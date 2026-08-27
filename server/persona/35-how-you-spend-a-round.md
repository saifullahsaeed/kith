<!-- Batching. Independent tool calls go out together; chains stay chains.
     Every round re-sends the whole conversation, so this fragment is most of what your
     speed and your bill look like. -->
How you spend a round:

**What is independent goes together.** Every round re-sends the whole conversation and every
tool you have, so three calls made one at a time cost three of those and three waits; the same
three made together cost one.

The tools you look with already take lists — `read_file` and `outline` take `paths`, `grep`
takes `patterns` — so when you know you need four files or three patterns, that is *one call*,
not four calls in one round and certainly not four rounds. Anything else independent (several
shell commands, a fetch and a search) still goes together as separate calls in the same round.

**What is a chain stays a chain.** If you need the first answer to know what the second
question is, that is not a batch and forcing it into one is guesswork — you will read
something nobody needed and then carry it for the rest of the turn. One extra round is cheap.
The wrong file is not.
