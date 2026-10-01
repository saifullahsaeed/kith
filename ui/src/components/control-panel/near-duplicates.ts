/**
 * Memories that say nearly the same thing — found here, in the browser, from the words alone.
 *
 * He saves a checkpoint, saves it again a few minutes later in slightly different words, and
 * again: on 10 August one Task #73 checkpoint went in eight times. Front-of-mind memories are
 * carried into every turn, so every copy is paid for on every turn, and nothing on the old
 * Memory page made the copies visible as copies — they were eight cards that each looked fine.
 *
 * Word overlap is enough to find them, and it is deliberately the dumb measure: the copies are
 * the *same sentences* with a word swapped or a clause dropped, not paraphrases, so embeddings
 * would add a server round trip to find what a set intersection already finds. Pure functions,
 * so the grouping is tested without a page around it.
 */

/** Words as they compare: lower-cased, markdown and punctuation gone, `’` folded into `'`. */
const WORD = /[\p{L}\p{N}]+(?:'[\p{L}\p{N}]+)*/gu;

interface Token {
  /** As written, for quoting back. */
  text: string;
  /** As compared. */
  norm: string;
}

function tokens(text: string): Token[] {
  const out: Token[] = [];
  for (const match of text.replace(/[’‘]/g, "'").matchAll(WORD)) {
    out.push({ text: match[0], norm: match[0].toLowerCase() });
  }
  return out;
}

/** The normalised words of a text, in order. */
export function words(text: string): string[] {
  return tokens(text).map((token) => token.norm);
}

/** Shared words over all words. Two empty texts are the same text. */
export function jaccard(a: ReadonlySet<string>, b: ReadonlySet<string>): number {
  if (a.size === 0 && b.size === 0) return 1;
  let shared = 0;
  for (const word of a) if (b.has(word)) shared++;
  return shared / (a.size + b.size - shared);
}

export function similarity(a: string, b: string): number {
  return jaccard(new Set(words(a)), new Set(words(b)));
}

/** The threshold the design commits to: four words in five shared. */
export const NEAR = 0.8;

/**
 * Groups of near-identical items, as lists of ids — only groups of two or more.
 *
 * Linked, not all-pairs: a copy that is 0.82 from one copy and 0.75 from another still belongs
 * with both, because what you would do about it is the same — keep one, forget the rest. Each
 * group keeps the input's order, and the groups come back largest first, newest first on a tie
 * (by the position of their first member in the input, which the caller sorts).
 */
export function nearDuplicateGroups<T extends { id: number; content: string }>(
  items: readonly T[],
  threshold = NEAR,
): number[][] {
  const sets = items.map((item) => new Set(words(item.content)));
  const parent = items.map((_, i) => i);
  const root = (i: number): number => {
    while (parent[i] !== i) {
      parent[i] = parent[parent[i]];
      i = parent[i];
    }
    return i;
  };
  for (let i = 0; i < items.length; i++) {
    // An empty memory is not a duplicate of every other empty memory worth showing.
    if (sets[i].size === 0) continue;
    for (let j = i + 1; j < items.length; j++) {
      if (sets[j].size === 0) continue;
      if (jaccard(sets[i], sets[j]) >= threshold) parent[root(j)] = root(i);
    }
  }
  const byRoot = new Map<number, number[]>();
  items.forEach((_, i) => {
    const r = root(i);
    const list = byRoot.get(r);
    if (list) list.push(i);
    else byRoot.set(r, [i]);
  });
  return [...byRoot.values()]
    .filter((members) => members.length > 1)
    .sort((a, b) => b.length - a.length || a[0] - b[0])
    .map((members) => members.map((i) => items[i].id));
}

/* ── What a copy says differently ───────────────────────────────────────── */

interface Hunk {
  /** Words the base has and the copy does not. */
  gone: string[];
  /** Words the copy has and the base does not. */
  added: string[];
}

/** Word-level diff (longest common subsequence), as the runs where the two part company. */
function hunks(base: Token[], other: Token[]): Hunk[] {
  const n = base.length;
  const m = other.length;
  // lcs[i][j] = LCS length of base[i:] and other[j:], flattened.
  const width = m + 1;
  const lcs = new Uint16Array((n + 1) * width);
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i * width + j] =
        base[i].norm === other[j].norm
          ? lcs[(i + 1) * width + j + 1] + 1
          : Math.max(lcs[(i + 1) * width + j], lcs[i * width + j + 1]);
    }
  }
  const out: Hunk[] = [];
  let open: Hunk | null = null;
  const flush = () => {
    if (open && (open.gone.length || open.added.length)) out.push(open);
    open = null;
  };
  let i = 0;
  let j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && base[i].norm === other[j].norm) {
      flush();
      i++;
      j++;
    } else if (j < m && (i >= n || lcs[i * width + j + 1] >= lcs[(i + 1) * width + j])) {
      (open ??= { gone: [], added: [] }).added.push(other[j].text);
      j++;
    } else {
      (open ??= { gone: [], added: [] }).gone.push(base[i].text);
      i++;
    }
  }
  flush();
  return out;
}

/** A run of words, quoted, cut at six. */
function quote(run: string[]): string {
  const cut = run.length > 6 ? `${run.slice(0, 6).join(" ")}…` : run.join(" ");
  return `“${cut}”`;
}


/** Words too common to count as "saying something". */
const FILLER = new Set(
  "a an and are as at be but by for from has have he his in is it its of on or so that the their them then this to was were which with".split(
    " ",
  ),
);

const filler = (run: string[]) => run.every((word) => FILLER.has(word.toLowerCase()));

/** A word or three traded for a word or three: a rewording, never news. */
const swap = (part: Hunk) => part.gone.length > 0 && part.added.length > 0 && Math.max(part.gone.length, part.added.length) <= 3;

/**
 * Where two copies part company, and whether either says something the other does not.
 *
 * "Says something" is two or more words that are new to the other copy, are not filler, and do
 * not sit in a swap. That last part is what keeps "Canonical" → "Current" from reading as news:
 * on his checkpoints every copy renamed something, and counting those made every copy look like
 * it held a fact the others had lost.
 */
function compare(base: string, other: string) {
  const a = tokens(base);
  const b = tokens(other);
  const parts = hunks(a, b);
  // An "and" here, a "the" there: not a difference anyone reading would call one.
  const real = parts.filter((part) => !(filler(part.gone) && filler(part.added)));
  const edits = real.filter((part) => !swap(part));
  const inA = new Set(a.map((token) => token.norm));
  const inB = new Set(b.map((token) => token.norm));
  const fresh = (run: string[], known: Set<string>) =>
    run.map((word) => word.toLowerCase()).filter((word) => !known.has(word) && !FILLER.has(word));
  const richest = (runs: string[][], known: Set<string>) =>
    runs.reduce<{ run: string[]; score: number }>(
      (best, run) => {
        const score = fresh(run, known).length;
        return score > best.score || (score === best.score && run.length > best.run.length) ? { run, score } : best;
      },
      { run: [], score: -1 },
    ).run;
  const added = new Set(edits.flatMap((part) => fresh(part.added, inA)));
  const gone = new Set(edits.flatMap((part) => fresh(part.gone, inB)));
  return {
    parts,
    real,
    adds: added.size >= 2,
    leaves: gone.size >= 2,
    /** Which way the bigger difference runs: more new words in `other` than lost from `base`. */
    mostlyAdds: added.size > gone.size,
    // Quote the run carrying the most of what is new, not merely the longest one.
    addedRun: richest(edits.map((part) => part.added), inA),
    goneRun: richest(edits.map((part) => part.gone), inB),
  };
}

/**
 * One line on how `other` differs from `base`, in the design's words:
 * "Word for word the same", "Says “sequence” where this says “order”", "Leaves out “…”".
 *
 * What it reports is what you would lose by merging, so a copy that only moves the same facts
 * around says that ("Reworded — same facts") rather than naming the longest run of changed words,
 * which on his checkpoints was usually "Canonical" — true, and no help deciding anything.
 *
 * `name` is what `base` is called in the sentence — "this" in the reader, where you are looking
 * at it, "#38" in the merge dialog, where you are choosing between them.
 */
export function describeDifference(base: string, other: string, name = "this"): string {
  const { parts, real, adds, leaves, mostlyAdds, addedRun, goneRun } = compare(base, other);
  if (parts.length === 0) return "Word for word the same";
  if (real.length === 0) return "Only small words differ";
  if (real.length === 1 && real[0].gone.length && real[0].added.length) {
    return `Says ${quote(real[0].added)} where ${name} says ${quote(real[0].gone)}`;
  }
  if (!adds && !leaves) return "Reworded — same facts";
  const adding = `adds ${quote(addedRun)}`;
  const leaving = `leaves out ${quote(goneRun)}`;
  // Many small edits read as a rewording; say so, then the bigger of the two things it changed —
  // both, on a line this narrow, is two halves of a sentence cut off.
  if (real.length >= 3) return `Reworded; ${adds && (!leaves || mostlyAdds) ? adding : leaving}`;
  const said = [adds ? adding : "", leaves ? leaving : ""].filter(Boolean).join("; ");
  return said.charAt(0).toUpperCase() + said.slice(1);
}

/**
 * Whether `other` says something `base` does not.
 *
 * A swapped word ("sequence" for "order") or a renamed thing is not that: merging loses nothing.
 * A clause about a task that was still open is: you would want to read it before it goes.
 */
export function addsSomething(base: string, other: string): boolean {
  return compare(base, other).adds;
}
