import { describe, expect, it } from "vitest";

import {
  ancestorsOf,
  askLine,
  bareUrl,
  blocksOf,
  domainOf,
  joinPath,
  lineTotal,
  modifiedLabel,
  parentOf,
  passageWindows,
  passagesOf,
  sortEntries,
  sourceKind,
  visibleEntries,
} from "./library";

/* The reader numbers passages the way the server stores them, so the split has to be the server's.
 *
 * These fixtures came from running `_chunks` itself — extracted from `server/kith/services/
 * sources.py` with `ast` and executed on the same five texts, on 2026-10-01. If the server's
 * chunking changes, these fail, which is the point: the gutter would otherwise go on numbering
 * seams that no longer exist. */
const texts: Record<string, () => string> = {
  // Paragraphs: every window can end on a blank line.
  t1: () =>
    Array.from({ length: 40 }, (_, i) => `Paragraph ${i} ` + "word ".repeat((i * 7) % 50) + "end.").join("\n\n"),
  // Sentences and no paragraphs: windows end on ". ".
  t2: () =>
    Array.from({ length: 300 }, (_, i) => `Sentence number ${i} says ${"x".repeat((i * 13) % 37)}.`).join(" "),
  // Nowhere to cut at all: hard 1,200-character windows.
  t3: () => "abcdefghij".repeat(260),
  // Characters outside the BMP — Python counts code points, so the port must too.
  t4: () =>
    Array.from({ length: 30 }, (_, i) => `Café 🚀 part ${i}: ` + "déjà vu ".repeat((i * 5) % 40) + "fin.").join("\n\n"),
  t5: () => "x".repeat(2100),
};

const server: Record<string, { count: number; lens: number[]; heads: string[]; tails: string[] }> = {
  t1: {
    count: 6,
    lens: [1170, 1177, 1192, 1191, 1180, 382],
    heads: ["Paragraph ", "d word wor", "word word ", "word word ", "word word ", "d word wor"],
    tails: [" word end.", " word end.", " word end.", " word end.", " word end.", " word end."],
  },
  t2: {
    count: 13,
    lens: [1191, 1161, 1193, 1180, 1170, 1168, 1196, 1195, 1189, 1184, 1165, 1171, 994],
    heads: ["Sentence n", "ys xxxxxxx", "xxxxxxxxxx", "x. Sentenc", ". Sentence", "r 117 says", "xxxxxxxx. ", "Sentence n", "er 186 say", "xxxxxxxxxx", "xxxxxxx. S", ". Sentence", "number 277"],
    tails: ["xxxxxxxxx.", "xxxxxxxxx.", "xxxxxxxxx.", " says xxx.", "ys xxxxxx.", "xxxxxxxxx.", "xxxxxxxxx.", "xxxxxxxxx.", "xxxxxxxxx.", "xxxxxxxxx.", "xxxxxxxxx.", "xxxxxxxxx.", "9 says xx."],
  },
  t3: { count: 3, lens: [1200, 1200, 500], heads: ["abcdefghij", "abcdefghij", "abcdefghij"], tails: ["abcdefghij", "abcdefghij", "abcdefghij"] },
  t4: {
    count: 5,
    lens: [985, 1179, 1040, 1139, 859],
    heads: ["Café 🚀 par", "déjà vu dé", "déjà vu dé", "vu fin.\n\nC", "vu déjà vu"],
    tails: ["jà vu fin.", "jà vu fin.", "jà vu fin.", "t 24: fin.", "jà vu fin."],
  },
  t5: { count: 2, lens: [1200, 1050], heads: ["xxxxxxxxxx", "xxxxxxxxxx"], tails: ["xxxxxxxxxx", "xxxxxxxxxx"] },
};

describe("passages", () => {
  for (const name of Object.keys(texts)) {
    it(`splits ${name} exactly where the server does`, () => {
      const text = texts[name]();
      const cps = Array.from(text);
      const chunks = passageWindows(text).map((w) => cps.slice(w.start, w.stop).join("").trim());
      const want = server[name];
      expect(chunks).toHaveLength(want.count);
      expect(chunks.map((c) => Array.from(c).length)).toEqual(want.lens);
      expect(chunks.map((c) => Array.from(c).slice(0, 10).join(""))).toEqual(want.heads);
      expect(chunks.map((c) => Array.from(c).slice(-10).join(""))).toEqual(want.tails);
    });
  }

  it("shows each passage's own text once, so the reader reads as the original", () => {
    const text = texts.t1();
    const { passages, total } = passagesOf(text);
    expect(total).toBe(6);
    // The overlap is not printed twice: stitched back together it is the text, give or take
    // the whitespace at each seam.
    const squash = (s: string) => s.replace(/\s+/g, " ").trim();
    expect(squash(passages.map((p) => p.text).join(" "))).toBe(squash(text));
    expect(passages.map((p) => p.n)).toEqual([1, 2, 3, 4, 5, 6]);
  });

  it("keeps a short source as one passage, and an empty one as none", () => {
    expect(passagesOf("  A note.  ")).toEqual({ passages: [{ n: 1, text: "A note." }], total: 1 });
    expect(passagesOf("   ")).toEqual({ passages: [], total: 0 });
  });

  it("draws fences, headings and paragraphs", () => {
    expect(blocksOf("# Title\n\nSome text\nmore.\n\n```yaml\non:\n\n  push\n```\nAfter.")).toEqual([
      { type: "heading", text: "Title" },
      { type: "para", text: "Some text\nmore." },
      { type: "code", text: "on:\n\n  push" },
      { type: "para", text: "After." },
    ]);
  });
});

describe("sources", () => {
  it("tells a link, a paste and a dropped file apart", () => {
    expect(sourceKind({ origin: "https://docs.github.com/en", title: "Events" })).toBe("link");
    expect(sourceKind({ origin: "pasted", title: "CodeRabbit pricing page" })).toBe("text");
    expect(sourceKind({ origin: "pasted", title: "api.md" })).toBe("file");
    expect(sourceKind({ origin: "pasted", title: "Version 2.0 notes" })).toBe("text");
  });

  it("asks about a source by id, after whatever you had typed, and only once", () => {
    const source = { id: 12, title: "Events that trigger workflows" };
    expect(askLine("", source)).toBe('About source #12 ("Events that trigger workflows"): ');
    expect(askLine("Compare these.  ", source)).toBe(
      'Compare these.\n\nAbout source #12 ("Events that trigger workflows"): ',
    );
    expect(askLine('About source #12 ("Events…"): why', source)).toBeNull();
    // #120 is a different source.
    expect(askLine("About source #120: x", source)).not.toBeNull();
  });

  it("names a link by its site", () => {
    expect(domainOf("https://www.docs.github.com/en/rest?x=1")).toBe("docs.github.com");
    expect(bareUrl("https://docs.github.com/en/rest/pulls/reviews/?x=1")).toBe("docs.github.com/en/rest/pulls/reviews");
    expect(domainOf("pasted")).toBe("pasted");
  });
});

describe("files", () => {
  const entries = [
    { name: "b.ts", type: "file", size: 10, modified: 300 },
    { name: ".git", type: "dir", size: 0, modified: 900 },
    { name: "a.ts", type: "file", size: 50, modified: 100 },
    { name: "src", type: "dir", size: 0, modified: 200 },
  ];

  it("puts folders first, then the chosen column", () => {
    expect(sortEntries(entries, "name").map((e) => e.name)).toEqual([".git", "src", "a.ts", "b.ts"]);
    expect(sortEntries(entries, "modified").map((e) => e.name)).toEqual([".git", "src", "b.ts", "a.ts"]);
    expect(sortEntries(entries, "size").map((e) => e.name)).toEqual([".git", "src", "a.ts", "b.ts"]);
  });

  it("hides dot-files unless asked, and filters by name", () => {
    expect(visibleEntries(entries, { showHidden: false, filter: "" }).map((e) => e.name)).toEqual(["b.ts", "a.ts", "src"]);
    expect(visibleEntries(entries, { showHidden: true, filter: "GI" }).map((e) => e.name)).toEqual([".git"]);
  });

  it("walks paths the way the listing API spells them", () => {
    expect(ancestorsOf("a/b/c")).toEqual(["a", "a/b", "a/b/c"]);
    expect(ancestorsOf(".")).toEqual([]);
    expect(joinPath(".", "x")).toBe("x");
    expect(joinPath("a/b", "x")).toBe("a/b/x");
    expect(parentOf("a/b")).toBe("a");
    expect(parentOf("a")).toBe(".");
  });

  it("counts lines the way an editor does", () => {
    expect(lineTotal("")).toBe(0);
    expect(lineTotal("a")).toBe(1);
    expect(lineTotal("a\nb\n")).toBe(2);
    expect(lineTotal("a\nb\n\n")).toBe(3);
  });

  it("says today and yesterday with the time, and a date after a week", () => {
    const now = new Date(2026, 8, 30, 15, 0);
    const at = (d: Date) => Math.floor(d.getTime() / 1000);
    expect(modifiedLabel(at(new Date(2026, 8, 30, 10, 36)), now)).toMatch(/^Today, /);
    expect(modifiedLabel(at(new Date(2026, 8, 29, 18, 32)), now)).toMatch(/^Yesterday, /);
    expect(modifiedLabel(at(new Date(2026, 7, 1, 9, 0)), now)).not.toMatch(/,/);
    expect(modifiedLabel(0, now)).toBe("");
  });
});
