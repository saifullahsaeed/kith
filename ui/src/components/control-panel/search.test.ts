/**
 * The palette's search, without the palette: what a query finds, in what order, how the match is
 * shown, and what is offered when it finds nothing. Real shapes from his snapshot (DATA.md,
 * 2026-09-30), cut down to the rows these cases need.
 */
import { describe, expect, it } from "vitest";

import type { ConversationSummary } from "@/lib/backend";
import type { BrainSnapshot } from "@/lib/backend/brain";
import {
  anywhere,
  buildEntries,
  editDistance,
  filesFrom,
  filesFromHits,
  groups,
  marks,
  mergeFiles,
  search,
  searchedLine,
  snippet,
  suggest,
  terms,
  vocabulary,
} from "./search";

const project = (id: number, name: string, status: string, directory: string | null) => ({
  id,
  name,
  description: "",
  status,
  directory,
  created_at: "2026-09-30T08:00:00Z",
  updated_at: "2026-09-30T09:00:00Z",
  tasks_planning: 0,
  tasks_approved: 0,
  tasks_working: 0,
  tasks_done: 0,
  tasks_dropped: 0,
  tasks_active: 0,
  tasks_total: 0,
});

const snap = {
  counts: {},
  projects: [
    project(35, "Ironline Haulage — operations portal", "archived", "/Users/saifullahsaeed/Kith/ironline-haulage"),
    project(29, "Blastwave — 3D explosion simulator", "active", "/Users/saifullahsaeed/Kith/blastwave"),
  ],
  memories: [
    {
      id: 114,
      content:
        "Ironline Haulage (#35) sign-in direction, settled 2026-09-30: light and clean. Deep green accent (#0C6B4E) on white/soft-grey.",
      tags: ["design", "ironline", "preference"],
      importance: 7,
      level: "recall",
      created_at: "2026-09-30T09:17:51Z",
      project_id: 35,
    },
    {
      id: 113,
      content:
        "~/Kith is a single git repository covering every project folder in it. Committing from inside a subfolder (e.g. ~/Kith/ironline-haulage) stages untracked work from every OTHER project too.",
      tags: ["git", "kith-folder", "gotcha"],
      importance: 8,
      level: "recall",
      created_at: "2026-09-30T08:44:51Z",
    },
  ],
  journal: [
    {
      id: 709,
      entry: "Third attempt at the Ironline sign-in. First two rejected, so I asked two questions in one call.",
      created_at: "2026-09-30T09:17:42Z",
    },
    {
      id: 700,
      entry: "Nuclear effects: blast, thermal and fireball from published scaling laws. The nuclear model is validated.",
      created_at: "2026-09-22T11:35:46Z",
    },
  ],
  tasks: [
    {
      id: 213,
      goal: "Nuclear effects: blast, thermal and fireball from published scaling laws",
      status: "working",
      priority: "high",
      description: "",
      created_by: "kith",
      project_id: 29,
      created_at: "2026-09-20T00:00:00Z",
      updated_at: "2026-09-29T00:00:00Z",
      outputs: 2,
    },
    {
      id: 262,
      goal: "Sign-in screen for the operations portal, in its own brand",
      status: "done",
      priority: "normal",
      description: "",
      created_by: "kith",
      project_id: 35,
      created_at: "2026-09-30T00:00:00Z",
      updated_at: "2026-09-30T09:00:00Z",
      outputs: 7,
    },
  ],
  reminders: [],
  messages: [],
  sources: [],
  schedules: [],
} as unknown as BrainSnapshot;

const chats = [
  {
    id: "c1",
    title: "make me design for login page a transport company no…",
    projectId: 35,
    messages: 6,
    updatedAt: "2026-09-30T09:17:58Z",
    lastSaid: "",
  },
] as unknown as ConversationSummary[];

const files = filesFrom({
  ".": [
    { name: "ironline-haulage", type: "dir", size: 0, modified: 1790761163 },
    { name: ".DS_Store", type: "file", size: 14340, modified: 1790626460 },
  ],
  "ironline-haulage/.kith/screens": [{ name: "console-sign-in.html", type: "file", size: 6860, modified: 1790761163 }],
});

const entries = buildEntries(snap, chats, files, "/Users/saifullahsaeed/Kith");

describe("search", () => {
  it("finds a word in every kind it appears in, and counts each", () => {
    const result = search(entries, "ironline");
    expect(result.counts).toMatchObject({ project: 1, memory: 2, journal: 1, task: 1, chat: 1, file: 2 });
    expect(result.total).toBe(8);
  });

  it("matches the project name and the path, not only the title", () => {
    const result = search(entries, "ironline");
    // Task 262's goal never says "Ironline" — its project does, on line two.
    expect(result.byKind.task.map((e) => e.id)).toEqual(["262"]);
    expect(result.byKind.task[0].meta).toContain("Ironline Haulage");
    // So does the chat, whose title is about a transport company.
    expect(result.byKind.chat[0].meta).toContain("In Ironline Haulage · 6 messages");
    // And the file, whose name doesn't, by its folder.
    expect(result.byKind.file.map((e) => e.id)).toContain("ironline-haulage/.kith/screens/console-sign-in.html");
  });

  it("ranks a title that starts with the word above one that has it in the middle", () => {
    const result = search(entries, "ironline");
    expect(result.byKind.memory.map((e) => e.id)).toEqual(["114", "113"]);
  });

  it("requires every word, in any field", () => {
    expect(search(entries, "nuclear blastwave").byKind.task.map((e) => e.id)).toEqual(["213"]);
    expect(search(entries, "nuclear ironline").total).toBe(0);
  });

  it("finds a task by its number", () => {
    expect(search(entries, "#262").byKind.task.map((e) => e.id)).toEqual(["262"]);
  });

  it("skips listing noise like .DS_Store", () => {
    expect(entries.some((e) => e.kind === "file" && e.title === ".DS_Store")).toBe(false);
  });

  it("lists the newest of each kind for an empty query", () => {
    const result = search(entries, "  ");
    expect(result.byKind.journal.map((e) => e.id)).toEqual(["709", "700"]);
    expect(result.total).toBe(entries.length);
  });
});

describe("groups", () => {
  it("keeps the board's order and caps each group under All, saying how many more", () => {
    const many = buildEntries(
      snap,
      [],
      filesFrom({ ".": Array.from({ length: 13 }, (_, i) => ({ name: `ironline-${i}.html`, type: "file" as const, size: 1, modified: 0 })) }),
    );
    const shown = groups(search(many, "ironline"), "all");
    expect(shown.map((g) => g.kind)).toEqual(["project", "memory", "journal", "task", "file"]);
    const filesGroup = shown.find((g) => g.kind === "file")!;
    expect(filesGroup.count).toBe(13);
    expect(filesGroup.rows).toHaveLength(3);
    expect(filesGroup.more).toBe("10 more files");
  });

  it("shows one kind in full when its chip is on", () => {
    const shown = groups(search(entries, "ironline"), "memory");
    expect(shown).toHaveLength(1);
    expect(shown[0].rows).toHaveLength(2);
    expect(shown[0].more).toBe("");
  });
});

describe("marks and snippet", () => {
  it("marks every occurrence, case-insensitively, merging overlaps", () => {
    expect(marks("Ironline at ~/Kith/ironline-haulage", ["ironline"])).toEqual([
      { text: "Ironline", hit: true },
      { text: " at ~/Kith/", hit: false },
      { text: "ironline", hit: true },
      { text: "-haulage", hit: false },
    ]);
    expect(marks("haulage", ["haul", "aula"])).toEqual([
      { text: "haula", hit: true },
      { text: "ge", hit: false },
    ]);
  });

  it("starts a long text a few words before the match", () => {
    const text = snap.memories[1].content;
    const cut = snippet(text, ["ironline"]);
    expect(cut.startsWith("…")).toBe(true);
    expect(cut).toContain("ironline-haulage");
    expect(cut.length).toBeLessThan(text.length);
    // Never mid-word.
    expect(cut[1]).not.toBe(" ");
    expect(text.includes(cut.slice(1))).toBe(true);
  });

  it("leaves a text alone when the match is already near its start", () => {
    expect(snippet("Ironline Haulage sign-in", ["ironline"])).toBe("Ironline Haulage sign-in");
  });
});

describe("did you mean", () => {
  const vocab = vocabulary(entries);
  const found = (word: string) => anywhere(entries, word);

  it("counts a swapped pair as one edit", () => {
    expect(editDistance("nuclaer", "nuclear")).toBe(1);
    expect(editDistance("kitten", "sitting")).toBe(3);
    expect(editDistance("abc", "abcdef", 2)).toBe(3);
  });

  it("offers the nearest known word for one that matches nothing", () => {
    expect(suggest("nuclaer", vocab, found)).toBe("nuclear");
    expect(suggest("irnoline", vocab, found)).toBe("ironline");
  });

  it("leaves words that already match alone, and gives up when nothing is close", () => {
    expect(suggest("ironl nuclaer", vocab, found)).toBe("ironl nuclear");
    expect(suggest("zzzzqqq", vocab, found)).toBeNull();
  });

  // Seen on his real journal: "kitten" came back as "written" — two edits, and not a typo of it.
  it("only offers a word two edits away when it starts with the same letter", () => {
    expect(suggest("kitten", new Map([["written", 40]]), () => false)).toBeNull();
    expect(suggest("kitten", new Map([["written", 40], ["mitten", 1]]), () => false)).toBe("mitten");
    expect(suggest("kitten", new Map([["written", 40], ["kitchen", 1]]), () => false)).toBe("kitchen");
  });

  it("says what was searched", () => {
    const totals = search(entries, "").counts;
    expect(searchedLine("journal", totals)).toBe("Searched all 2 journal entries.");
    expect(searchedLine("file", totals)).toBe("Searched the names of all 2 files.");
    expect(searchedLine("all", totals)).toBe(
      "Searched 2 projects, 2 memories, 2 journal entries, 2 tasks, 1 chat and the names of 2 files.",
    );
  });
});

describe("terms", () => {
  it("lowercases, splits and dedupes", () => {
    expect(terms("  Ironline  ironline Sign-in ")).toEqual(["ironline", "sign-in"]);
  });
});

describe("files from his folder's search", () => {
  const hits = filesFromHits([
    { path: "backoff-plan", name: "backoff-plan", kind: "name", type: "dir", size: 0, modified: 10 },
    { path: "src/retry.py", name: "retry.py", kind: "content", type: "file", line: 41, text: "def retry_with_backoff():", size: 9, modified: 20 },
    { path: "src/retry.py", name: "retry.py", kind: "content", type: "file", line: 52, text: "backoff *= 2", size: 9, modified: 20 },
  ]);

  it("knows the folder each hit is in", () => {
    expect(hits.map((h) => h.folder)).toEqual([".", "src", "src"]);
    expect(hits[1]).toMatchObject({ match: "content", line: 41, text: "def retry_with_backoff():" });
  });

  it("keeps one row per name and one per line, the server's over the listing's", () => {
    const cached = filesFrom({ ".": [{ name: "backoff-plan", type: "dir", size: 0, modified: 1 }, { name: "other.md", type: "file", size: 1, modified: 1 }] });
    const merged = mergeFiles(cached, hits);
    expect(merged.map((f) => `${f.path}:${f.line ?? ""}`)).toEqual(["backoff-plan:", "src/retry.py:41", "src/retry.py:52", "other.md:"]);
    expect(merged[0].modified).toBe(10);
  });

  it("finds a line by what it says, ranks a name above it, and keys each line apart", () => {
    const found = search(buildEntries(null, [], hits, "/Users/someone/Kith"), "backoff");
    const rows = found.byKind.file;
    expect(rows[0].key).toBe("file:backoff-plan");
    // The two lines rank as any two rows do — a line that starts with the word first.
    expect(rows.slice(1).map((e) => e.key)).toEqual(["file:src/retry.py#L52", "file:src/retry.py#L41"]);
    // The line is what line two shows, so it is what the match is bolded in.
    expect(rows[2].meta).toBe("def retry_with_backoff():");
  });

  it("says it looked inside files only when it did", () => {
    const totals = search(entries, "").counts;
    expect(searchedLine("file", totals, true)).toBe("Searched the names and contents of everything in his folder.");
    expect(searchedLine("all", totals, true)).toBe(
      "Searched 2 projects, 2 memories, 2 journal entries, 2 tasks, 1 chat and the names and contents of everything in his folder.",
    );
  });
});
