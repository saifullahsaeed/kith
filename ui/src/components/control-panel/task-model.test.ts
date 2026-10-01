import { describe, expect, it } from "vitest";

import type { ChecklistItem, Deliverable, Task } from "@/lib/backend/brain";
import {
  chatAbout,
  namedTasks,
  parsePlan,
  previewPlan,
  shortTitle,
  size,
  withoutLeadIn,
  stepThrough,
  stepWindow,
  stepsGist,
  summariseTable,
  taskActivity,
  waitingPlans,
  waited,
} from "./task-model";

// Cut from his real plan for #164 (`.kith/work/task-164.md`), keeping every shape it uses: the
// title, a metadata preamble, prose with a quote, a verdict table with a bold conclusion after it,
// a bold-code list, and the short sections the board lists as "Also in the plan".
const PLAN_164 = `# #164 — The company switcher works

**Milestone 69** · project #16 Sadeef One · filed 17 Sep 2026

## Why

A tenant can hold several companies, and an employee can belong to more than one. But
nothing in the SPA ever sends \`X-Company-Id\`, so the middleware's active company is
**always the primary membership** — \`AppRail.tsx:155\` says so in its own comment:

> Nothing in the client sends \`X-Company-Id\`.

So a person in two companies can only ever act in one of them.

## What I checked (verified this turn, not recalled)

| Thing | Where | State |
|---|---|---|
| Middleware reads the header | \`shared/tenancy/middleware.py:70\` | ✅ built |
| Non-member company → 403, no context set | \`middleware.py:82\` | ✅ built |
| \`/auth/me\` returns \`active_company_id\` | \`identity/api/auth.py:98\` | ✅ built |
| \`/auth/me\` returns \`memberships\` (id + name) | \`auth.py:99-107\` | ✅ built — its comment says *"the membership list"* |
| The picker | — | ❌ **does not exist** |
| Anything sets the header | \`grep -rn 'Company-Id' frontend/src\` | ❌ **never set** |

**So this is a client-only change.** The server has been ready for it since the
middleware was written.

## What changes

- **\`frontend/src/shared/stores/\`** — the chosen company id. This is genuinely UI state
  (which of my companies am I looking at), so it belongs in Zustand.
- **\`frontend/src/shared/api/client.ts\`** — \`api()\` adds \`X-Company-Id\` from that
  store.
- **\`frontend/src/app/shell/TopBar.tsx\`** — the switcher. It has **no** company UI at
  all today.
- **The cache.** Every cached list belongs to the company you just left.

## What I am still assuming

- That \`is_primary\` is what the middleware falls back to.

## How I'll know it worked

1. A test that a request carries \`X-Company-Id\` once a company is chosen.

## Not in scope

Renaming or reordering companies — that is #137 (\`directory\` writes). This task only moves the pointer.

## Open question

That is #166's problem (the see-all grant) and worth checking against it before shipping.
`;

describe("parsePlan", () => {
  const plan = parsePlan(PLAN_164);

  it("drops the title, keeps the preamble apart, and cuts at every ##", () => {
    expect(plan.preamble).toBe("**Milestone 69** · project #16 Sadeef One · filed 17 Sep 2026");
    expect(plan.sections.map((one) => one.title)).toEqual([
      "Why",
      "What I checked (verified this turn, not recalled)",
      "What changes",
      "What I am still assuming",
      "How I'll know it worked",
      "Not in scope",
      "Open question",
    ]);
  });

  it("reads each section as the blocks Markdown would", () => {
    expect(plan.sections[0].blocks.map((one) => one.kind)).toEqual(["paragraph", "quote", "paragraph"]);
    const table = plan.sections[1].blocks[0];
    expect(table.kind).toBe("table");
    if (table.kind !== "table") return;
    expect(table.header).toEqual(["Thing", "Where", "State"]);
    expect(table.rows).toHaveLength(6); // the |---| separator is not a row
    const list = plan.sections[2].blocks[0];
    expect(list.kind === "list" && list.items).toHaveLength(4);
    // A wrapped item is one item, its continuation joined on.
    expect(list.kind === "list" && list.items[0]).toContain("so it belongs in Zustand.");
  });

  it("does not take a # inside a code fence for a heading", () => {
    const fenced = parsePlan("# Title\n\n## One\n\n```sh\n# not a heading\n## nor this\n```\n\n## Two\n\ntext");
    expect(fenced.sections.map((one) => one.title)).toEqual(["One", "Two"]);
    expect(fenced.sections[0].blocks).toEqual([{ kind: "code", raw: "```sh\n# not a heading\n## nor this\n```" }]);
  });

  it("keeps ### inside its section, and reads a second # as a section", () => {
    const nested = parsePlan("# T\n\n## What changes\n\n### Backend\n\n- a\n\n# T again\n\nnote");
    expect(nested.sections.map((one) => one.title)).toEqual(["What changes", "T again"]);
    expect(nested.sections[0].blocks.map((one) => one.kind)).toEqual(["heading", "list"]);
  });

  it("treats a plan with no title as starting at its first section", () => {
    expect(parsePlan("\n\n## Step 3\n- one").sections[0].title).toBe("Step 3");
  });
});

describe("summariseTable", () => {
  const plan = parsePlan(PLAN_164);
  const section = plan.sections[1];
  const table = section.blocks.find((one) => one.kind === "table");

  it("counts verdicts per row, and quotes his word only when every row agrees on it", () => {
    if (!table || table.kind !== "table") throw new Error("no table");
    const summary = summariseTable(section, table);
    expect(summary).toEqual({
      rows: 6,
      noun: "facts",
      note: "verified this turn",
      // "✅ built" four times is "4 built"; "❌ does not exist" and "❌ never set" are not one word.
      marks: [
        { tone: "yes", count: 4, label: "built" },
        { tone: "no", count: 2, label: "missing" },
      ],
      conclusion: "So this is a client-only change",
    });
  });

  it("takes a bold name for emphasis, not for the conclusion", () => {
    const named = parsePlan("## Known\n\n| a | b |\n|---|---|\n| x | ✅ real |\n\nSee **file.viewall** — **the grant is per company, not per tenant.**");
    const block = named.sections[0].blocks[0];
    if (block.kind !== "table") throw new Error("no table");
    expect(summariseTable(named.sections[0], block).conclusion).toBe("the grant is per company, not per tenant");
  });

  it("calls a table without verdicts rows, and concludes nothing it was not told", () => {
    const plain = parsePlan("## Sizes\n\n| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |");
    const block = plain.sections[0].blocks[0];
    if (block.kind !== "table") throw new Error("no table");
    expect(summariseTable(plain.sections[0], block)).toMatchObject({ rows: 2, noun: "rows", note: "", marks: [], conclusion: "" });
  });
});

describe("previewPlan", () => {
  it("draws what fits in the card and names the rest — the board's split for #164", () => {
    const preview = previewPlan(parsePlan(PLAN_164));
    expect(preview.shown.map((one) => [one.kind, one.title])).toEqual([
      ["prose", "Why"],
      ["table", "What I checked (verified this turn, not recalled)"],
      ["prose", "What changes"],
    ]);
    expect(preview.rest).toEqual([3, 4, 5, 6]);
    const why = preview.shown[0];
    // The quote is left out of the lead; the paragraphs either side of it run together.
    expect(why.kind === "prose" && why.lead).toMatch(/^A tenant can hold.*So a person in two companies/s);
    expect(why.kind === "prose" && why.lead).not.toContain("Nothing in the client sends");
    // …and so is the clause that introduced it, which would otherwise point at nothing.
    expect(why.kind === "prose" && why.lead).toContain("**always the primary membership**. So a person");
    const changes = preview.shown[2];
    expect(changes.kind === "prose" && changes.items).toHaveLength(4);
  });

  it("always shows the first section, even past the budget, with a long list folded", () => {
    const long = parsePlan(`## One\n\n${Array.from({ length: 30 }, (_, i) => `- item ${i}`).join("\n")}\n\n## Two\n\nx`);
    const preview = previewPlan(long, 50);
    expect(preview.shown).toHaveLength(1);
    expect(preview.shown[0].kind === "prose" && preview.shown[0].more).toBe(25);
    expect(preview.rest).toEqual([1]);
  });

  it("keeps the plan's order: once a section misses, everything after it is listed", () => {
    const plan = parsePlan(`## A\n\n${"word ".repeat(400)}\n\n## B\n\n| a |\n|---|\n| 1 |\n\n## C\n\nshort`);
    // A table row cannot be cut down, so B goes to the footer — and C with it, though C is short.
    expect(previewPlan(plan, 100).rest).toEqual([1, 2]);
  });

  it("draws the section that does not fit whole as far as it goes, rather than leaving a gap", () => {
    // His #220: a three-line "Why", then a "What changes" of a lead and nine items.
    const plan = parsePlan(
      `## Why\n\n${"word ".repeat(60)}\n\n## What changes\n\n${"lead ".repeat(30)}\n\n${"- item\n".repeat(9)}\n## Next\n\nx`,
    );
    const preview = previewPlan(plan);
    expect(preview.shown.map((one) => one.title)).toEqual(["Why", "What changes"]);
    const cut = preview.shown[1];
    if (cut.kind !== "prose") throw new Error("not prose");
    // 238px: Why takes 82, leaving 156 — 12 + 18 for the heading, 44 for a two-line lead, and
    // two items with the "+7 more" line under them (4 + 3 × 20). A third item would need 176.
    expect(cut.items.length + cut.more).toBe(9);
    expect(cut.items.length).toBe(2);
    expect(preview.rest).toEqual([2]);
  });
});

describe("withoutLeadIn", () => {
  it("drops the lead-in to a quote the preview leaves out, and nothing else", () => {
    expect(withoutLeadIn("Nothing sends it, so it is always the primary — `AppRail.tsx:155` says so:")).toBe(
      "Nothing sends it, so it is always the primary.",
    );
    expect(withoutLeadIn("The server is ready. Here is the route that proves it:")).toBe("The server is ready.");
    expect(withoutLeadIn("Run this:")).toBe("Run this:");
    expect(withoutLeadIn("No colon — nothing to cut.")).toBe("No colon — nothing to cut.");
  });
});

describe("shortTitle", () => {
  it("drops asides and what follows a dash", () => {
    expect(shortTitle("What I checked (verified this turn, not recalled)")).toBe("What I checked");
    expect(shortTitle("⚠ BLOCKED ON A DECISION — option A or B below")).toBe("BLOCKED ON A DECISION");
    expect(shortTitle("Outcome (11 Sep 2026) — built and verified")).toBe("Outcome");
  });
});

describe("namedTasks", () => {
  it("finds the other tasks the plan names, where it names them, and skips project numbers", () => {
    expect(namedTasks(parsePlan(PLAN_164), 164)).toEqual([
      { id: 137, section: "Not in scope" },
      { id: 166, section: "Open question" },
    ]);
  });

  it("does not read a colour or its own id as a task", () => {
    expect(namedTasks(parsePlan("## A\n\ncolour #2A2723, see #9 and #164, milestone #70"), 164)).toEqual([
      { id: 9, section: "A" },
    ]);
  });
});

const step = (done: boolean, text = "step"): Pick<ChecklistItem, "done" | "text"> => ({ done: done ? 1 : 0, text });

describe("stepWindow", () => {
  it("shows a short list whole, rather than folding one line into '1 more'", () => {
    expect(stepWindow([step(false), step(false), step(false), step(false), step(false)])).toEqual({ start: 0, end: 5 });
  });

  it("opens on the first steps of a plan nothing has been done on", () => {
    expect(stepWindow(Array.from({ length: 7 }, () => step(false)))).toEqual({ start: 0, end: 4 });
  });

  it("follows him down the list: the last step done, then what is next", () => {
    const items = [true, true, true, true, false, false, false, false].map((done) => step(done));
    expect(stepWindow(items)).toEqual({ start: 3, end: 7 });
    // Six of seven done — the window ends at the end rather than running past it.
    expect(stepWindow([true, true, true, true, true, true, false].map((done) => step(done)))).toEqual({ start: 3, end: 7 });
  });
});

describe("stepsGist", () => {
  it("is one clause per folded step — the #164 steps the board folds", () => {
    expect(
      stepsGist([
        "Clear the TanStack Query cache on switch — every cached list belongs to the company you just left",
        "Pin it: a test that a request carries the header, and a test that switching invalidates the cache",
        "Run the frontend suite, then screenshot the same route in two companies to show the data differs",
      ]),
    ).toBe("clear the TanStack Query cache on switch, pin it, run the frontend suite");
  });

  it("stops at the end of the first sentence", () => {
    expect(stepsGist(["Create src/physics/nuclear.ts. Scale blast radius by the cube-root law"])).toBe(
      "create src/physics/nuclear.ts",
    );
  });

  it("leaves a name at the start of a step alone", () => {
    expect(stepsGist(["TopBar.tsx gets the switcher", "Zustand store for it"])).toBe(
      "TopBar.tsx gets the switcher, zustand store for it",
    );
  });
});

const T0 = "2026-09-17T15:41:38.000Z";
const plus = (minutes: number) => new Date(Date.parse(T0) + minutes * 60_000).toISOString();
const item = (minutes: number, done = false): ChecklistItem => ({
  id: minutes,
  task_id: 164,
  text: "step",
  done: done ? 1 : 0,
  order_index: 0,
  created_at: plus(minutes),
});
const deliverable = (minutes: number, title: string): Deliverable => ({
  id: minutes,
  task_id: 164,
  kind: "file",
  title,
  content: `screens/${title}.html`,
  created_at: plus(minutes),
});

describe("taskActivity", () => {
  const base = {
    created_at: T0,
    updated_at: plus(1),
    created_by: "kith",
    status: "planning",
    plan: "# plan",
    checklist: Array.from({ length: 7 }, () => item(0)),
    deliverables: [] as Deliverable[],
  };

  it("reads #164: filed with its plan and steps, then four designs attached as one move", () => {
    const events = taskActivity(
      { ...base, deliverables: ["A", "B", "C", "D"].map((x, i) => deliverable(213 + i, `Option ${x}`)) },
      () => true,
    );
    expect(events).toEqual([
      { who: "kith", text: "Filed this with its plan and seven steps", at: T0 },
      { who: "kith", text: "Attached four designs: Option A, Option B, Option C, Option D", at: plus(213) },
    ]);
  });

  it("says what you added, and does not credit anyone with steps added later", () => {
    const events = taskActivity({ ...base, created_by: "user", plan: "", checklist: [item(0), item(90), item(91)] });
    expect(events).toEqual([
      { who: "you", text: "Added this task", at: T0 },
      { who: null, text: "Added two steps", at: plus(90) },
    ]);
  });

  it("dates the plan by its file when the server says when it was written", () => {
    const events = taskActivity({ ...base, created_by: "user", checklist: [], plan_modified: plus(45) });
    expect(events.map((event) => event.text)).toEqual(["Added this task", "Wrote the plan"]);
  });

  it("claims only what updated_at can prove about a status", () => {
    const working = taskActivity({ ...base, status: "working", updated_at: plus(300), checklist: [item(0, true), item(0)] });
    expect(working.at(-1)).toEqual({ who: "kith", text: "Last moved it on — 1 of 2 steps done", at: plus(300) });
    const done = taskActivity({ ...base, status: "done", updated_at: plus(1) });
    expect(done.at(-1)?.text).toBe("Marked it done");
    // Still planning: a later touch is not a status change, so it is not an event.
    expect(taskActivity({ ...base, updated_at: plus(600) })).toHaveLength(1);
  });
});

const task = (id: number, priority: string, status = "planning", project: number | null = 16) =>
  ({ id, priority, status, project_id: project }) as Task;

describe("waitingPlans and stepThrough", () => {
  const tasks = [
    task(170, "normal"),
    task(164, "high"),
    task(150, "normal"),
    task(166, "high"),
    task(99, "high", "done"),
    task(12, "high", "planning", 3),
  ];

  it("is the project's Planning column, in the board's order", () => {
    expect(waitingPlans(tasks, 16).map((one) => one.id)).toEqual([164, 166, 150, 170]);
  });

  it("steps to the neighbours and stops at the ends", () => {
    const list = waitingPlans(tasks, 16);
    expect(stepThrough(list, 164)).toEqual({ prev: null, next: 166 });
    expect(stepThrough(list, 150)).toEqual({ prev: 166, next: 170 });
    // From a task that is not waiting, J starts at the top and K at the bottom.
    expect(stepThrough(list, 99)).toEqual({ prev: 170, next: 164 });
  });
});

describe("small readings", () => {
  it("sizes to one decimal", () => {
    expect(size(4099)).toBe("4.0 KB");
    expect(size(12797)).toBe("12.5 KB");
    expect(size(512)).toBe("512 B");
  });

  it("says how long something waited", () => {
    const now = Date.parse(T0);
    expect(waited(plus(-13 * 24 * 60), now)).toBe("13 days");
    expect(waited(plus(-60), now)).toBe("1 hour");
    expect(waited(plus(-5), now)).toBe("5 min");
  });

  it("carries the task into the chat it starts", () => {
    expect(chatAbout({ id: 164, goal: "The switcher works" }, "  use a panel  ", true)).toBe(
      "Before I approve #164 (“The switcher works”), change the plan:\n\nuse a panel",
    );
    expect(chatAbout({ id: 164, goal: "The switcher works" }, "why B?", false)).toBe(
      "About #164 (“The switcher works”):\n\nwhy B?",
    );
  });
});
