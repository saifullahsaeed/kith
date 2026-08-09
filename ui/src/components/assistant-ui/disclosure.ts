/**
 * How long a disclosure in the thread takes to open, in one place.
 *
 * Three components animate the same way — reasoning, a tool group, a tool fallback — and each
 * held its own `const ANIMATION_DURATION = 200`. The number is used twice in each: once to tell
 * `useScrollLock` how long to hold the scroll position, and once as the `--animation-duration`
 * the CSS reads. So it was three files that had to agree on one number, and disagreeing does
 * not look like a bug — it looks like the thread jumping when you expand one kind of block and
 * not another.
 */
export const ANIMATION_DURATION = 200;
