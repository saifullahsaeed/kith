import { describe, expect, it } from "vitest";

import { dayKey, heatLevel, longDate, monthCells, relativeDay } from "./day-format";
import { firstLine, keptLabel, keptMemories, mentionedFiles, mentionedProjects, spokenName, wordCount } from "./journal-links";

const PROJECTS = [
  { id: 35, name: "Ironline Haulage — operations portal", status: "active", directory: "/Users/s/Kith/ironline-haulage" },
  { id: 40, name: "Microsoft calendar consent hardening", status: "active", directory: "/Users/s/Desktop/ai-play" },
  { id: 41, name: "Project Management Test", status: "active", directory: "/Users/s/Kith/project-management-test" },
  { id: 7, name: "Asterion (working codename)", status: "active" },
  { id: 6, name: "Sadeef AI", status: "active" },
  { id: 28, name: "Sadeef AI connector runtime", status: "active" },
  { id: 25, name: "placeholder", status: "archived" },
  { id: 12, name: "Sadeef Client Portal", status: "archived", updated_at: "2026-08-01" },
  { id: 30, name: "Sadeef Client Portal", status: "active", updated_at: "2026-07-01" },
];

describe("mentionedProjects", () => {
  it("knows a project by the part of its name he says", () => {
    expect(spokenName("Ironline Haulage — operations portal")).toBe("Ironline Haulage");
    expect(spokenName("Asterion (working codename)")).toBe("Asterion");
    expect(spokenName("Mission planner: flat map, multiple missiles")).toBe("Mission planner");
    const found = mentionedProjects("Third attempt at the Ironline Haulage sign-in. Asterion can wait.", PROJECTS);
    expect(found.map((p) => p.id)).toEqual([35, 7]);
  });

  it("knows a project by its first word when that word is its own coined name", () => {
    expect(mentionedProjects("Third attempt at the Ironline sign-in.", PROJECTS).map((p) => p.id)).toEqual([35]);
    // A vendor, and a generic opener, are not names — even though each starts one project.
    expect(mentionedProjects("Microsoft SSO hard-coded via env", PROJECTS)).toEqual([]);
    expect(mentionedProjects("archived Project #11 and moved on", PROJECTS)).toEqual([]);
  });

  it("matches 'project #35' by id", () => {
    expect(mentionedProjects("so I created project #35 with its own directory", PROJECTS).map((p) => p.id)).toEqual([35]);
  });

  it("prefers the longer name when one sits inside another", () => {
    expect(mentionedProjects("Shipped the Sadeef AI connector runtime today", PROJECTS).map((p) => p.id)).toEqual([28]);
    expect(mentionedProjects("RAG audit for Sadeef AI (#6, ai-play)", PROJECTS).map((p) => p.id)).toEqual([6]);
  });

  it("does not claim ordinary words, or half a word", () => {
    expect(mentionedProjects("left a placeholder in the README", PROJECTS)).toEqual([]);
    expect(mentionedProjects("the Asterionic thing", PROJECTS)).toEqual([]);
  });

  it("says a duplicated name once, choosing the active row", () => {
    expect(mentionedProjects("Sadeef Client Portal again", PROJECTS).map((p) => p.id)).toEqual([30]);
  });
});

describe("mentionedFiles", () => {
  it("lists files and folders he names, once each, in order", () => {
    const text =
      "Collision in ~/Kith/pr-review-action. I wrote src/diff.ts, package.json and README.md. Both pass check_design.py; Node.js is fine.";
    expect(mentionedFiles(text)).toEqual(["~/Kith/pr-review-action", "src/diff.ts", "package.json", "README.md", "check_design.py"]);
  });
});

describe("keptMemories", () => {
  const entry = new Date("2026-09-30T09:17:42.496Z").getTime();
  const memories = [
    { id: 114, created_at: "2026-09-30T09:17:51.916Z" },
    { id: 113, created_at: "2026-09-30T08:44:51.478Z" },
    { id: 115, created_at: "2026-09-30T09:17:30.000Z" },
  ];

  it("finds what was saved within a minute of the entry, nearest first", () => {
    expect(keptMemories(entry, memories).map(({ memory }) => memory.id)).toEqual([114, 115]);
  });

  it("says how far apart, and which way", () => {
    expect(keptLabel(9_420)).toBe("Saved 9 seconds after this entry");
    expect(keptLabel(-1_000)).toBe("Saved 1 second before this entry");
    expect(keptLabel(200)).toBe("Saved with this entry");
  });
});

describe("entry text", () => {
  it("counts words, not markup", () => {
    expect(wordCount("## Heading\n\n- one item — and `code`\n\n```ts\nx\n```")).toBe(7);
  });

  it("takes the first line as plain text", () => {
    expect(firstLine("\n## **Collision** in `~/Kith`\nmore")).toBe("Collision in ~/Kith");
  });
});

describe("day-format", () => {
  it("lays September 2026 out Monday-first: one blank, thirty days, four blanks", () => {
    const cells = monthCells(2026, 8);
    expect(cells).toHaveLength(35);
    expect(cells[0]).toBeNull();
    expect(cells[1]).toBe(1);
    expect(cells.slice(31)).toEqual([null, null, null, null]);
  });

  it("tints by steps, not by the busiest day", () => {
    expect([0, 1, 2, 3, 4, 7, 8, 44].map(heatLevel)).toEqual([0, 1, 2, 2, 3, 3, 4, 4]);
  });

  it("keys and names days in local time", () => {
    const ms = new Date(2026, 8, 30, 23, 59).getTime();
    expect(dayKey(ms)).toBe("2026-09-30");
    expect(longDate(ms, ms)).toMatch(/^\w+ 30 \w+$/);
    expect(relativeDay(ms, ms)).toBe("Today");
    expect(relativeDay(new Date(2026, 8, 29).getTime(), ms)).toBe("Yesterday");
    expect(relativeDay(new Date(2026, 8, 26).getTime(), ms)).toBe("4 days ago");
  });
});
