import { describe, expect, it } from "vitest";

import type { BrainSnapshot, TimelineEvent } from "@/lib/backend/brain";
import { activityStrip, breakdown, buildDays, chipTotals, foldDay, joinPages, spanLabel, toLifeEvents } from "./lifetime-fold";
import type { LifeEvent } from "./lifetime-fold";

/** A local time on 30 September 2026, so day boundaries are the reader's own. */
const at = (hour: number, minute = 0, day = 30) => new Date(2026, 8, day, hour, minute).getTime();

let seq = 0;
const ev = (kind: LifeEvent["kind"], ms: number, extra: Partial<LifeEvent> = {}): LifeEvent => ({
  key: `${kind}:${++seq}`,
  kind,
  ms,
  text: `${kind} ${seq}`,
  project: "",
  ...extra,
});

describe("foldDay", () => {
  it("folds every output on the same task into one row, even with other things in between", () => {
    const events = [
      ev("output", at(12, 21), { taskId: 7, text: "Sign in — livelier" }),
      ev("memory", at(12, 17)),
      ev("output", at(12, 9), { taskId: 7, text: "Sign in — light" }),
      ev("journal", at(11, 44)),
      ev("output", at(11, 41), { taskId: 7, text: "Sign in — first" }),
    ];
    const rows = foldDay(events);
    expect(rows.map((row) => row.kind)).toEqual(["output", "memory", "journal"]);
    // The job sits where its newest output is, and holds all three, newest first.
    expect(rows[0]!.events.map((e) => e.text)).toEqual(["Sign in — livelier", "Sign in — light", "Sign in — first"]);
  });

  it("keeps outputs on different tasks apart", () => {
    const rows = foldDay([ev("output", at(10), { taskId: 1 }), ev("output", at(9), { taskId: 2 })]);
    expect(rows).toHaveLength(2);
  });

  it("falls back to the task's name when the alert's task is not known", () => {
    const rows = foldDay([ev("output", at(10), { on: "Pace screens" }), ev("output", at(9), { on: "Pace screens" })]);
    expect(rows).toHaveLength(1);
    expect(rows[0]!.events).toHaveLength(2);
  });

  it("folds back-to-back events of one kind on one project, and only those", () => {
    const rows = foldDay([
      ev("memory", at(16, 1), { project: "Harrier" }),
      ev("memory", at(16, 0), { project: "Harrier" }),
      ev("memory", at(15, 59), { project: "Localis" }),
      ev("journal", at(15, 41), { project: "Localis" }),
      ev("memory", at(15, 30), { project: "Localis" }),
    ]);
    expect(rows.map((row) => [row.kind, row.project, row.events.length])).toEqual([
      ["memory", "Harrier", 2],
      ["memory", "Localis", 1],
      ["journal", "Localis", 1],
      ["memory", "Localis", 1],
    ]);
  });

  it("does not let an output that joined a job further up break a run below it", () => {
    const rows = foldDay([
      ev("output", at(13), { taskId: 4 }),
      ev("task", at(12, 30), { project: "Pace" }),
      ev("output", at(12, 29), { taskId: 4 }),
      ev("task", at(12, 28), { project: "Pace" }),
    ]);
    expect(rows.map((row) => [row.kind, row.events.length])).toEqual([
      ["output", 2],
      ["task", 2],
    ]);
  });

  it("keys a run by its oldest event, so the key survives the run growing", () => {
    const older = ev("memory", at(9));
    const before = foldDay([ev("memory", at(10)), older]);
    const after = foldDay([ev("memory", at(11)), ev("memory", at(10)), older]);
    expect(after[0]!.key).toBe(before[0]!.key);
  });
});

describe("buildDays", () => {
  it("never folds across midnight, and counts a day before folding", () => {
    const days = buildDays([
      ev("output", at(0, 5, 30), { taskId: 9 }),
      ev("output", at(23, 55, 29), { taskId: 9 }),
      ev("output", at(23, 50, 29), { taskId: 9 }),
    ]);
    expect(days.map((day) => [day.key, day.count, day.bursts.length])).toEqual([
      ["2026-09-30", 1, 1],
      ["2026-09-29", 2, 1],
    ]);
  });

  it("sorts newest first whatever order the events arrive in", () => {
    const days = buildDays([ev("memory", at(9, 0, 28)), ev("memory", at(9, 0, 30)), ev("memory", at(9, 0, 29))]);
    expect(days.map((day) => day.key)).toEqual(["2026-09-30", "2026-09-29", "2026-09-28"]);
  });
});

describe("toLifeEvents", () => {
  const snap = {
    tasks: [
      { id: 223, goal: "CLI and container a team can run", status: "working", project_id: 31 },
      { id: 264, goal: "The complete Pace mobile screen set", status: "done", project_id: 33 },
    ],
    projects: [
      { id: 31, name: "Harrier", status: "active" },
      { id: 33, name: "AI personal finance — mobile design", status: "active" },
      { id: 35, name: "Ironline Haulage — operations portal", status: "active" },
    ],
    messages: [
      { id: 1261, kind: "delivered", link: "/tasks/223", body: "", read: 1, sender: "kith", created_at: "" },
      { id: 1260, kind: "asked", link: "/chat/abc", project_id: 31, body: "", read: 1, sender: "kith", created_at: "" },
      { id: 1200, kind: "note", link: null, body: "", read: 1, sender: "kith", created_at: "" },
    ],
  } as unknown as Pick<BrainSnapshot, "tasks" | "projects" | "messages">;

  const timeline: TimelineEvent[] = [
    { kind: "message", at: "2026-09-30T15:38:43Z", text: "Finished something on “CLI and container a team can run”: PR Review Report UI Design", meta: { id: 1261 } },
    { kind: "message", at: "2026-09-30T15:23:56Z", text: "I need an answer before I can carry on: Where would you like to publish this?", meta: { id: 1260 } },
    { kind: "message", at: "2026-09-30T15:00:00Z", text: "A note for you", meta: { id: 1200 } },
    { kind: "message", at: "2026-09-30T14:00:00Z", text: "Finished something on “The complete Pace mobile screen set”: 54 · Form error", meta: { id: 999 } },
    { kind: "task", at: "2026-09-30T11:25:40Z", text: "A notification can be raised by any app", meta: { id: 265, status: "working" } },
    { kind: "memory", at: "2026-09-30T09:17:51Z", text: "Ironline Haulage (#35) sign-in direction, settled", meta: { id: 114, level: "recall" } },
  ];

  const events = toLifeEvents(timeline, snap);

  it("reads a delivered alert as an output on its task, with the task's project", () => {
    expect(events[0]).toMatchObject({ kind: "output", text: "PR Review Report UI Design", taskId: 223, project: "Harrier" });
  });

  it("finds an output's task by name when the alert is not in the snapshot", () => {
    expect(events.find((e) => e.key === "output:999")).toMatchObject({ taskId: 264, project: "AI personal finance — mobile design" });
  });

  it("keeps his questions, with their chat, and drops notes", () => {
    expect(events.find((e) => e.kind === "asked")).toMatchObject({ text: "Where would you like to publish this?", link: "/chat/abc", project: "Harrier" });
    expect(events.some((e) => e.text === "A note for you")).toBe(false);
  });

  it("gives a memory the project it names", () => {
    expect(events.find((e) => e.kind === "memory")).toMatchObject({ project: "Ironline Haulage — operations portal", projectId: 35 });
  });

  it("keeps a task's status", () => {
    expect(events.find((e) => e.kind === "task")).toMatchObject({ taskId: 265, status: "working", project: "" });
  });
});

describe("pages", () => {
  const snap = { tasks: [{ id: 7, goal: "Pace screens", status: "working", project_id: 33 }], projects: [{ id: 33, name: "Pace", status: "active" }], messages: [] } as unknown as Pick<
    BrainSnapshot,
    "tasks" | "projects" | "messages"
  >;
  /** A delivered alert for task 7, `minute` minutes past noon on 30 September, local. */
  const output = (id: number, minute: number): TimelineEvent => ({
    kind: "message",
    at: new Date(at(12, minute)).toISOString(),
    text: `Finished something on “Pace screens”: screen ${id}`,
    meta: { id, kind: "delivered", link: "/tasks/7" },
  });
  // A job of six screens, and a page edge after the fourth — where the 500th event fell.
  const newer = { events: [output(6, 50), output(5, 40), output(4, 30), output(3, 20)] };
  const older = { events: [output(2, 10), output(1, 0), { kind: "journal", at: new Date(at(9)).toISOString(), text: "Morning", meta: { id: 1 } } as TimelineEvent] };

  it("folds a job split by a page edge into one row once both pages are loaded", () => {
    const first = buildDays(toLifeEvents(joinPages([newer]), snap));
    expect(first[0]!.bursts.map((burst) => [burst.kind, burst.events.length])).toEqual([["output", 4]]);

    const both = buildDays(toLifeEvents(joinPages([newer, older]), snap));
    expect(both[0]!.bursts.map((burst) => [burst.kind, burst.events.length])).toEqual([
      ["output", 6],
      ["journal", 1],
    ]);
    // The same row, grown — so a row you had open stays open as the older page lands under it.
    expect(both[0]!.bursts[0]!.key).toBe(first[0]!.bursts[0]!.key);
    expect(both[0]!.bursts[0]!.events.map((e) => e.text)).toEqual(["screen 6", "screen 5", "screen 4", "screen 3", "screen 2", "screen 1"]);
  });

  it("draws an event once even when two pages both hold it", () => {
    const overlapping = { events: [output(3, 20), ...older.events] };
    expect(joinPages([newer, overlapping]).filter((event) => event.meta.id === 3)).toHaveLength(1);
  });

  it("knows a question from a note without the snapshot, which holds only the newest messages", () => {
    const [asked] = toLifeEvents(
      [{ kind: "message", at: "2026-08-02T10:00:00Z", text: "Which font?", meta: { id: 5, kind: "asked", link: "/chat/x", projectId: 33 } }],
      snap,
    );
    expect(asked).toMatchObject({ kind: "asked", link: "/chat/x", project: "Pace" });
  });

  it("counts the chips over all of history from the server's totals", () => {
    const totals = chipTotals({
      totals: { task: 259, memory: 90, journal: 709, reminder: 24, message: 254 },
      message_kinds: { delivered: 101, asked: 68, stuck: 8, note: 74, reachout: 3 },
    });
    // Notes and reach-outs are not on this screen, so "All" does not count them.
    expect(totals).toEqual({ all: 259 + 101 + 90 + 709 + 24 + 68 + 8, tasks: 259, outputs: 101, memory: 90, journal: 709, reminders: 24 });
    // A server from before paging sends no totals, and the screen counts what it has.
    expect(chipTotals({ totals: null, message_kinds: null })).toBeNull();
  });
});

describe("activityStrip", () => {
  it("is thirty local days ending today, with counts by kind", () => {
    const now = at(15);
    const strip = activityStrip([ev("task", at(9)), ev("output", at(10)), ev("output", at(11)), ev("memory", at(9, 0, 1)), ev("memory", at(9, 0, 30 - 30))], now);
    expect(strip).toHaveLength(30);
    expect(strip[29]!.key).toBe("2026-09-30");
    expect(strip[0]!.key).toBe("2026-09-01");
    expect(strip[29]!.count).toBe(3);
    expect(strip[29]!.kinds).toEqual({ task: 1, output: 2 });
    expect(strip[0]!.count).toBe(1);
    // 31 August is outside the window and counted nowhere.
    expect(strip.reduce((sum, day) => sum + day.count, 0)).toBe(4);
  });
});

describe("phrases", () => {
  it("says a day's mix the way the tooltip does", () => {
    expect(breakdown({ task: 46, output: 22, memory: 4, journal: 4 })).toBe("46 tasks · 22 outputs · 4 memories · 4 journal");
    expect(breakdown({ memory: 1, asked: 2 })).toBe("1 memory · 2 questions");
  });

  it("says how long a run took", () => {
    expect(spanLabel(20_000)).toBe("in under a minute");
    expect(spanLabel(70_000)).toBe("in about a minute");
    expect(spanLabel(40 * 60_000)).toBe("in 40 minutes");
    expect(spanLabel(3 * 3_600_000)).toBe("over 3 hours");
  });
});
