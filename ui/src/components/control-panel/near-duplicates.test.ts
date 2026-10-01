import { describe, expect, it } from "vitest";

import { addsSomething, describeDifference, jaccard, nearDuplicateGroups, similarity, words } from "./near-duplicates";

// Shaped on his real 10 August checkpoints: the same sentences, a word swapped or a clause gone.
const CHECKPOINT =
  "Canonical checkpoint, 2026-08-10: Active work is Sadeef AI Task #73, the chat-orchestration refactor. " +
  "Provider-completion extraction shipped in commit 82376bf; full backend verification passed at 3302 passed/4 skipped. " +
  "Approved remaining order: extract tool-round state, then finish/accounting state, then run full backend verification and push.";

describe("words", () => {
  it("lower-cases, drops markdown and punctuation, keeps apostrophes inside a word", () => {
    expect(words("**Don’t** push `main` — ask (first)!")).toEqual(["don't", "push", "main", "ask", "first"]);
  });
});

describe("jaccard", () => {
  it("is shared over all", () => {
    expect(jaccard(new Set(["a", "b", "c"]), new Set(["b", "c", "d"]))).toBeCloseTo(0.5);
    expect(jaccard(new Set(), new Set())).toBe(1);
  });

  it("ignores case and punctuation between copies", () => {
    expect(similarity("Canonical checkpoint, 2026-08-10:", "canonical checkpoint 2026 08 10")).toBe(1);
  });
});

describe("nearDuplicateGroups", () => {
  const swapped = CHECKPOINT.replace("Approved remaining order", "Approved remaining sequence");
  const unrelated = "He prefers primary sources — vendor pricing pages and docs — over comparison articles.";

  it("groups copies at four words in five, and leaves the rest alone", () => {
    const groups = nearDuplicateGroups([
      { id: 38, content: CHECKPOINT },
      { id: 37, content: swapped },
      { id: 12, content: unrelated },
      { id: 36, content: CHECKPOINT },
    ]);
    expect(groups).toEqual([[38, 37, 36]]);
  });

  it("links through a middle copy even when the ends are further apart than the threshold", () => {
    const a = "one two three four five six seven eight nine ten";
    const b = "one two three four five six seven eight nine eleven"; // a↔b 9/11 ≈ 0.82
    const c = "one two three four five six seven eight twelve eleven"; // b↔c 0.82, a↔c 8/12 ≈ 0.67
    expect(similarity(a, c)).toBeLessThan(0.8);
    expect(nearDuplicateGroups([{ id: 1, content: a }, { id: 2, content: b }, { id: 3, content: c }])).toEqual([[1, 2, 3]]);
  });

  it("returns the biggest group first, and the one met first on a tie", () => {
    const x = "alpha beta gamma delta epsilon";
    const y = "zeta eta theta iota kappa";
    const groups = nearDuplicateGroups([
      { id: 1, content: y },
      { id: 2, content: x },
      { id: 3, content: x },
      { id: 4, content: y },
      { id: 5, content: x },
    ]);
    expect(groups).toEqual([
      [2, 3, 5],
      [1, 4],
    ]);
    expect(nearDuplicateGroups([{ id: 7, content: y }, { id: 8, content: x }, { id: 9, content: y }, { id: 10, content: x }])).toEqual([
      [7, 9],
      [8, 10],
    ]);
  });

  it("does not call two empty memories duplicates", () => {
    expect(nearDuplicateGroups([{ id: 1, content: "" }, { id: 2, content: "  " }])).toEqual([]);
  });
});

describe("describeDifference", () => {
  it("says when a copy is word for word the same, punctuation aside", () => {
    expect(describeDifference(CHECKPOINT, CHECKPOINT.replace("2026-08-10:", "2026-08-10 —"))).toBe("Word for word the same");
  });

  it("names a swapped word", () => {
    expect(describeDifference(CHECKPOINT, CHECKPOINT.replace("remaining order", "remaining sequence"))).toBe(
      "Says “sequence” where this says “order”",
    );
    expect(describeDifference(CHECKPOINT, CHECKPOINT.replace("order", "sequence"), "#38")).toBe(
      "Says “sequence” where #38 says “order”",
    );
  });

  it("says what a copy leaves out, and what it adds, cut at six words", () => {
    const shorter = CHECKPOINT.replace(" then run full backend verification and push", "");
    expect(describeDifference(CHECKPOINT, shorter)).toBe("Leaves out “then run full backend verification and…”");
    expect(describeDifference(shorter, CHECKPOINT)).toBe("Adds “then run full backend verification and…”");
  });

  it("calls many small edits a rewording", () => {
    const reworded = CHECKPOINT.replace("Canonical", "Current")
      .replace("Active work is", "The active thread is")
      .replace("shipped in", "landed in")
      .replace(" then run full backend verification and push", "");
    expect(describeDifference(CHECKPOINT, reworded)).toBe("Reworded; leaves out “then run full backend verification and…”");
  });
});

describe("addsSomething", () => {
  it("is false for a swapped word and true for a clause the other lacks", () => {
    const swapped = CHECKPOINT.replace("order", "sequence");
    expect(addsSomething(CHECKPOINT, swapped)).toBe(false);
    const more = `${CHECKPOINT} Website Task #83 is still open.`;
    expect(addsSomething(CHECKPOINT, more)).toBe(true);
    expect(addsSomething(more, CHECKPOINT)).toBe(false);
  });
});
