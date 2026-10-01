import { describe, expect, it } from "vitest";

import type { ConversationSummary, StoredTurn } from "@/lib/backend/conversations";
import type { ProjectOutput } from "@/lib/backend/brain";
import {
  codeRuns,
  extensionLabel,
  filesWritten,
  filterChats,
  insideProject,
  linkDomain,
  looksLikeALink,
  moveTarget,
  outputKind,
  plainText,
  rowWhen,
  sharedSummary,
  shortDay,
  sortOutputs,
  whatYouAsked,
} from "./project-page-model";

const NOW = new Date(2026, 9, 1, 15, 0); // 1 Oct 2026, 3 PM local

describe("shortDay / rowWhen", () => {
  it("names today and yesterday, and writes other days short", () => {
    expect(shortDay(new Date(2026, 9, 1, 9, 40).toISOString(), NOW)).toBe("Today");
    expect(shortDay(new Date(2026, 8, 30, 23, 0).toISOString(), NOW)).toBe("Yesterday");
    // Not "Tuesday": a weekday beside "Sep 18" in a column asks you to work out which came first.
    expect(shortDay(new Date(2026, 8, 22, 12, 0).toISOString(), NOW)).toBe("Sep 22");
    expect(shortDay(new Date(2025, 11, 3).toISOString(), NOW)).toBe("Dec 3, 2025");
    expect(shortDay("not a date", NOW)).toBe("");
  });

  it("puts the time on a row only when it was today", () => {
    expect(rowWhen(new Date(2026, 9, 1, 13, 9).toISOString(), NOW)).toBe("Today, 1:09 PM");
    expect(rowWhen(new Date(2026, 8, 23, 13, 9).toISOString(), NOW)).toBe("Sep 23");
  });
});

describe("moveTarget — the arrow keys follow the drag's rule", () => {
  it("moves one column either way", () => {
    expect(moveTarget("planning", 1, false)).toEqual({ status: "approved" });
    expect(moveTarget("approved", -1, false)).toEqual({ status: "planning" });
    expect(moveTarget("working", -1, false)).toEqual({ status: "approved" });
    expect(moveTarget("working", 1, false)).toEqual({ status: "done" });
  });

  it("refuses Working as a destination, and does not jump over it", () => {
    expect(moveTarget("approved", 1, false)).toEqual({ refused: expect.stringContaining("Working") });
    expect(moveTarget("done", -1, false)).toEqual({ refused: expect.stringContaining("Working") });
  });

  it("stops at the edges, and reaches Dropped only while it is shown", () => {
    expect(moveTarget("planning", -1, false)).toBeNull();
    expect(moveTarget("done", 1, false)).toBeNull();
    expect(moveTarget("done", 1, true)).toEqual({ status: "dropped" });
    expect(moveTarget("dropped", -1, true)).toEqual({ status: "done" });
    expect(moveTarget("dropped", -1, false)).toBeNull();
  });
});

const chat = (over: Partial<ConversationSummary>): ConversationSummary => ({
  id: "c",
  title: "hey",
  sessionId: "s",
  createdAt: "2026-10-01T11:19:00Z",
  updatedAt: "2026-10-01T13:09:00Z",
  messages: 2,
  lastSaid: "",
  transcript: "",
  projectId: 16,
  working: false,
  waiting: false,
  ...over,
});

describe("filterChats", () => {
  const items = [
    chat({ id: "a", title: "hey", working: true, lastSaid: "Wire the first real producer" }),
    chat({ id: "b", title: "make me a landing page", waiting: true }),
    chat({ id: "c", title: "Please check the CRM", working: true, waiting: true }),
    chat({ id: "d", title: "org page" }),
  ];

  it("keeps working, or waiting on you — and waiting wins when it is both", () => {
    expect(filterChats(items, "working", "").map((one) => one.id)).toEqual(["a"]);
    expect(filterChats(items, "waiting", "").map((one) => one.id)).toEqual(["b", "c"]);
    expect(filterChats(items, "all", "").length).toBe(4);
  });

  it("filters by title or by what he said, ignoring case", () => {
    expect(filterChats(items, "all", "PRODUCER").map((one) => one.id)).toEqual(["a"]);
    expect(filterChats(items, "all", "landing").map((one) => one.id)).toEqual(["b"]);
  });

  it("knows a pasted link from a title", () => {
    expect(looksLikeALink("<https://claude.ai/artifact/6JKF>")).toBe(true);
    expect(looksLikeALink("claude.ai/artifact/6JKF")).toBe(true);
    expect(looksLikeALink("make me a landing page")).toBe(false);
  });
});

const user = (text: string, at = ""): StoredTurn => ({ role: "user", at, parts: [{ kind: "text", text }] });

describe("whatYouAsked", () => {
  it("lists what you typed, in order, and counts the replies that only kept it going", () => {
    const { asks, left } = whatYouAsked([
      user("see current project and tell me the best method to add notifications", "t1"),
      { role: "assistant", parts: [{ kind: "text", text: "Here is a plan." }] },
      user("ok"),
      user("make me the backend for it please", "t2"),
      user("keep going"),
      { role: "system", parts: [{ kind: "text", text: "A reminder fired." }] },
      user("nice!"),
    ]);
    expect(asks).toEqual([
      { at: "t1", text: "see current project and tell me the best method to add notifications" },
      { at: "t2", text: "make me the backend for it please" },
    ]);
    expect(left).toBe(3);
  });
});

describe("filesWritten", () => {
  it("reads write, edit and multi-edit calls, newest first, each file once", () => {
    const tool = (name: string, args: Record<string, unknown>) => ({ kind: "tool" as const, id: name, name, arguments: args });
    const files = filesWritten([
      { role: "assistant", at: "11:47", parts: [tool("write_file", { path: ".kith/screens/notifications.html" })] },
      { role: "assistant", at: "12:24", parts: [tool("read_file", { paths: ["a.py"] }), tool("write_file", { path: "stage.py" })] },
      {
        role: "assistant",
        at: "13:05",
        parts: [tool("edit_files", { edits: [{ path: "apps.py" }, { path: "tasks.py" }] })],
      },
      { role: "assistant", at: "13:08", parts: [tool("edit_file", { path: "tasks.py" })] },
    ]);
    expect(files).toEqual([
      { path: "tasks.py", at: "13:08" },
      { path: "apps.py", at: "13:05" },
      { path: "stage.py", at: "12:24" },
      { path: ".kith/screens/notifications.html", at: "11:47" },
    ]);
  });
});

describe("insideProject", () => {
  it("reads relative to the project's folder, and with ~ outside it", () => {
    expect(insideProject("/Users/me/Kith/pf/.kith/screens/51.html", "/Users/me/Kith/pf")).toBe(".kith/screens/51.html");
    expect(insideProject("/Users/me/Kith/pf/.kith/a.md", "/Users/me/Kith/pf/")).toBe(".kith/a.md");
    expect(insideProject("/Users/me/Kith/other/a.md", "/Users/me/Kith/pf")).toBe("~/Kith/other/a.md");
    // A sibling whose name starts the same is not inside.
    expect(insideProject("/Users/me/Kith/pf2/a.md", "/Users/me/Kith/pf")).toBe("~/Kith/pf2/a.md");
  });
});

const output = (over: Partial<ProjectOutput>): ProjectOutput => ({
  id: 1,
  task_id: 264,
  kind: "file",
  title: "t",
  content: "/x/a.md",
  created_at: "2026-09-30T09:40:00Z",
  task_goal: "g",
  ...over,
});

describe("outputs", () => {
  it("tells a design from a file, a link and a note", () => {
    expect(outputKind(output({ content: "/x/.kith/screens/51-sync.html" }))).toBe("design");
    expect(outputKind(output({ content: "/x/brief.md" }))).toBe("file");
    expect(outputKind(output({ kind: "link", content: "https://example.com" }))).toBe("link");
    expect(outputKind(output({ kind: "text", content: "notes" }))).toBe("note");
  });

  it("sorts by when it was filed, not by when the file last moved", () => {
    const items = [
      output({ id: 1, title: "51 · Sync", created_at: "2026-09-30T09:40:00Z", modified: "2026-09-30T12:00:00Z" }),
      output({ id: 2, title: "system.json", created_at: "2026-09-30T09:53:00Z", modified: "2026-09-30T09:53:00Z" }),
      output({ id: 3, title: "10 · Home", created_at: "2026-09-30T09:40:00Z", bytes: 900 }),
    ];
    expect(sortOutputs(items, "newest").map((one) => one.id)).toEqual([2, 1, 3]);
    expect(sortOutputs(items, "oldest").map((one) => one.id)).toEqual([1, 3, 2]);
    // Numbers in names sort as numbers: 10 after 51 would be alphabetical, not what you meant.
    expect(sortOutputs(items, "name").map((one) => one.title)).toEqual(["10 · Home", "51 · Sync", "system.json"]);
    expect(sortOutputs(items, "largest")[0].id).toBe(3);
  });

  it("labels a file by its extension, and a link by its domain", () => {
    expect(extensionLabel(".kith/design/brief.md")).toBe("MD");
    expect(extensionLabel("system.json")).toBe("JSON");
    expect(extensionLabel("Makefile")).toBe("FILE");
    expect(linkDomain("https://www.example.com/a/b")).toBe("example.com");
    expect(linkDomain("not a url")).toBe("not a url");
  });
});

describe("sharedSummary", () => {
  const base = { folder: "/x", added: [], updated: [], kept: [], blocked: "", changed_ago: 60 };

  it("says nothing when nothing differs, or there is no folder", () => {
    expect(sharedSummary(null)).toBeNull();
    expect(sharedSummary(base)).toBeNull();
    expect(sharedSummary({ ...base, folder: "", added: ["a"] })).toBeNull();
  });

  it("is one line: how many are waiting, then what kind of difference", () => {
    expect(sharedSummary({ ...base, added: ["a", "b", "c"], kept: ["d", "e", "f", "g"] })).toEqual({
      headline: "3 tasks in the shared folder aren't on this board",
      detail: "3 new from someone else, 4 newer here",
      waiting: 3,
      blocked: false,
    });
    expect(sharedSummary({ ...base, updated: ["a"] })?.headline).toBe("1 task in the shared folder isn't on this board");
    expect(sharedSummary({ ...base, kept: ["a"] })).toMatchObject({ waiting: 0, headline: "The shared folder differs from this board" });
  });

  it("puts a folder it could not read first", () => {
    expect(sharedSummary({ ...base, added: ["a"], blocked: "a merge is in progress" })).toMatchObject({
      blocked: true,
      detail: "a merge is in progress",
    });
  });
});

describe("plainText / codeRuns", () => {
  it("flattens his markdown to one line for a clamp, keeping code marks for the mono runs", () => {
    expect(plainText("## Goal\n- **Ship** it\n- see [the doc](http://x)")).toBe("Goal Ship it see the doc");
    expect(codeRuns("`npm run check` exits 0")).toEqual([
      { code: true, text: "npm run check" },
      { code: false, text: " exits 0" },
    ]);
  });
});
