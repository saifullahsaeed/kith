/**
 * The rules the Projects list is drawn from (2026-10-01 redesign): which group a project sits
 * in, which tile it answers to, what order it comes in, and when an empty board folds away.
 */
import { describe, expect, it } from "vitest";

import type { ConversationSummary } from "@/lib/backend";
import type { BrainSnapshot } from "@/lib/backend/brain";
import { groupRows, meetsNeed, plainLine, rowFor, shortDate, sortRows, whenLabel } from "./projects-model";
import { LOOSE } from "./types";

type Project = BrainSnapshot["projects"][number];
type Task = BrainSnapshot["tasks"][number];

function project(id: number, name: string, updated = "2026-08-01T10:00:00Z"): Project {
  return {
    id,
    name,
    description: "",
    status: "active",
    directory: null,
    created_at: "2026-07-01T10:00:00Z",
    updated_at: updated,
    tasks_planning: 0,
    tasks_approved: 0,
    tasks_working: 0,
    tasks_done: 0,
    tasks_dropped: 0,
    tasks_active: 0,
    tasks_total: 0,
  };
}

let nextTask = 1;
function task(projectId: number | null, status: string, updated = "2026-08-01T10:00:00Z"): Task {
  return {
    id: nextTask++,
    goal: `task ${nextTask}`,
    status,
    priority: "normal",
    description: "",
    created_by: "kith",
    project_id: projectId,
    created_at: updated,
    updated_at: updated,
  };
}

function chat(projectId: number, updatedAt: string, title = "a chat"): ConversationSummary {
  return {
    id: `c-${updatedAt}`,
    title,
    sessionId: "s",
    createdAt: updatedAt,
    updatedAt,
    messages: 2,
    lastSaid: "",
    transcript: "",
    projectId,
    working: false,
    waiting: false,
  };
}

describe("a row", () => {
  it("is last active at whichever moved latest: the project, a task, or a conversation", () => {
    const p = project(1, "Vigil", "2026-09-01T00:00:00Z");
    expect(rowFor(p, [task(1, "done", "2026-09-10T00:00:00Z")], []).lastActive).toBe(Date.parse("2026-09-10T00:00:00Z"));
    expect(
      rowFor(p, [task(1, "done", "2026-09-10T00:00:00Z")], [chat(1, "2026-09-20T00:00:00Z")]).lastActive,
    ).toBe(Date.parse("2026-09-20T00:00:00Z"));
    expect(rowFor(p, [], []).lastActive).toBe(Date.parse("2026-09-01T00:00:00Z"));
  });

  it("puts its newest conversation first whatever order the listing came in", () => {
    const row = rowFor(project(1, "Vigil"), [], [chat(1, "2026-09-01T00:00:00Z", "old"), chat(1, "2026-09-22T00:00:00Z", "new")]);
    expect(row.chats.map((c) => c.title)).toEqual(["new", "old"]);
  });

  it("is an empty board only with no tasks, no conversations, and the conversations known", () => {
    const p = project(1, "placeholder");
    expect(rowFor(p, [], []).empty).toBe(true);
    expect(rowFor(p, [task(1, "dropped")], []).empty).toBe(false);
    expect(rowFor(p, [], [chat(1, "2026-09-01T00:00:00Z")]).empty).toBe(false);
    // Still loading: not folded on a guess.
    expect(rowFor(p, [], [], false).empty).toBe(false);
  });

  it("for the tray is 'No project', never empty, and has no folder", () => {
    const row = rowFor(null, [task(null, "planning")], []);
    expect(row.ref).toBe(LOOSE);
    expect(row.name).toBe("No project");
    expect(row.empty).toBe(false);
    expect(row.directory).toBeNull();
  });

  it("reads its description as one line of plain text", () => {
    expect(plainLine("A **bold** plan with `code` and a [link](https://x.y).\n\n## Next\nMore")).toBe(
      "A bold plan with code and a link. Next More",
    );
  });
});

describe("the tiles as filters", () => {
  const counts = { planning: 2, approved: 0, working: 1, done: 0, dropped: 0, total: 3 };
  it("match on the column each one counts", () => {
    expect(meetsNeed(counts, "waiting")).toBe(true);
    expect(meetsNeed(counts, "working")).toBe(true);
    expect(meetsNeed(counts, "next")).toBe(false);
    expect(meetsNeed(counts, "done")).toBe(false);
    expect(meetsNeed(counts, null)).toBe(true);
  });
});

describe("sorting", () => {
  const a = rowFor(project(1, "Alpha", "2026-09-01T00:00:00Z"), [task(1, "planning")], []);
  const b = rowFor(project(2, "beta", "2026-09-20T00:00:00Z"), [task(2, "planning"), task(2, "planning")], []);
  const c = rowFor(project(3, "Gamma", "2026-09-10T00:00:00Z"), [], []);
  const tray = rowFor(null, [task(null, "planning"), task(null, "planning"), task(null, "planning")], []);

  it("by recency puts the most recently active first, and the tray last", () => {
    expect(sortRows([tray, a, c, b], "recent").map((r) => r.name)).toEqual(["beta", "Gamma", "Alpha", "No project"]);
  });
  it("by what waits on you puts the most plans first", () => {
    expect(sortRows([a, c, b, tray], "waiting").map((r) => r.name)).toEqual(["beta", "Alpha", "Gamma", "No project"]);
  });
  it("by name ignores case", () => {
    expect(sortRows([c, b, a], "name").map((r) => r.name)).toEqual(["Alpha", "beta", "Gamma"]);
  });
});

describe("grouping", () => {
  const needs = rowFor(project(1, "Sadeef One"), [task(1, "planning"), task(1, "working")], []);
  const working = rowFor(project(2, "Sprout"), [task(2, "working")], []);
  const quiet = rowFor(project(3, "Rangekeeper"), [task(3, "done"), task(3, "approved")], []);
  const empty = rowFor(project(4, "placeholder"), [], []);

  it("splits active projects by what they need, and folds the empty boards apart", () => {
    const { groups, folded } = groupRows([needs, working, quiet, empty], true);
    expect(groups.map((g) => [g.key, g.rows.map((r) => r.name)])).toEqual([
      ["needs", ["Sadeef One"]],
      ["working", ["Sprout"]],
      ["quiet", ["Rangekeeper"]],
    ]);
    expect(folded.map((r) => r.name)).toEqual(["placeholder"]);
  });

  it("keeps the other tabs as one group, still folding the empty boards", () => {
    const { groups, folded } = groupRows([needs, quiet, empty], false);
    expect(groups).toHaveLength(1);
    expect(groups[0]!.key).toBe("all");
    expect(groups[0]!.rows.map((r) => r.name)).toEqual(["Sadeef One", "Rangekeeper"]);
    expect(folded.map((r) => r.name)).toEqual(["placeholder"]);
  });
});

describe("dates", () => {
  const now = new Date(2026, 9, 1, 15, 0).getTime();
  it("say the hour today, 'Yesterday', then a short date", () => {
    // ICU puts a narrow no-break space before "PM"; the words are what is being checked.
    expect(whenLabel(new Date(2026, 9, 1, 13, 9).getTime(), now, "en-US").replace(/\s/g, " ")).toBe("Today, 1:09 PM");
    expect(whenLabel(new Date(2026, 8, 30, 23, 0).getTime(), now, "en-US")).toBe("Yesterday");
    expect(whenLabel(new Date(2026, 8, 22, 9, 0).getTime(), now, "en-US")).toBe("Sep 22");
  });
  it("carry the year only when it is not this one", () => {
    expect(shortDate(new Date(2026, 7, 11).getTime(), now, "en-US")).toBe("Aug 11");
    expect(shortDate(new Date(2025, 7, 11).getTime(), now, "en-US")).toBe("Aug 11, 2025");
    expect(whenLabel(0, now)).toBe("");
  });
});
