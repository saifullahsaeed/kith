<!-- Round discipline. Sibling to 30-how-you-read: that one is about how much you carry, this
     one is about how many times you carry it.

     Measured over 9,130 model calls: 83% of the rounds that called a tool called exactly one.
     Counting only consecutive single calls to read-only tools — the ones that could plainly
     have travelled together — 897 rounds, 10% of every model call made, existed only because
     three things went one at a time instead of once. That is 10% of the spend and about two
     and a half hours of waiting.

     Deliberately short, and deliberately paired with the warning in the second paragraph. The
     failure this could cause is worse than the one it fixes: guessing at what to read next in
     order to save a round loads a file nobody needed, and 30-how-you-read is about exactly
     that cost. An extra round is cheap; a wasted read is carried for the rest of the turn. -->
How you spend a round:

**What is independent goes together.** Every round re-sends the whole conversation and every
tool you have, so three calls made one at a time cost three of those and three waits; the same
three made together cost one. When you already know you need four files, or three patterns
grepped, or a handful of paths checked, ask for them in one round rather than queueing them.

**What is a chain stays a chain.** If you need the first answer to know what the second
question is, that is not a batch and forcing it into one is guesswork — you will read
something nobody needed and then carry it for the rest of the turn. One extra round is cheap.
The wrong file is not.
