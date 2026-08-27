import { describe, expect, it, vi } from "vitest";

import { mark, soonest, summary, until, type State } from "./tray";

const idle: State = { working: 0, waiting: [], costUsd: 0, nextWake: null };

/**
 * The whole point of the menu bar is the glance.
 *
 * Before this the icon was one static template image whatever was happening — mid-turn, idle, or
 * stopped waiting on an answer it could not continue without — so the only way to find out was
 * to open the menu, and the menu was reading a deleted endpoint through an unauthenticated fetch
 * and therefore said "Kith" forever.
 */
describe("the mark beside the icon", () => {
  it("is nothing at all when he is idle", () => {
    // An app that always shows something in the menu bar is an app you stop seeing.
    expect(mark(idle)).toBe("");
  });

  it("is a quiet dot while he works", () => {
    expect(mark({ ...idle, working: 2 })).toBe(" ·");
  });

  it("is a number the moment anything needs you", () => {
    const waiting = { ...idle, waiting: [{ label: "a" }, { label: "b" }] };
    expect(mark(waiting)).toBe(" 2");
  });

  it("prefers the number over the dot when he is both working and blocked", () => {
    // Two conversations running and one of them stopped to ask: the question is the thing you
    // can do something about, so it wins the one character available.
    expect(mark({ ...idle, working: 2, waiting: [{ label: "asked you" }] })).toBe(" 1");
  });
});

describe("the header line", () => {
  it("says he is idle rather than saying his name", () => {
    expect(summary(idle)).toBe("Here, nothing running");
  });

  it("counts conversations only when there is more than one", () => {
    expect(summary({ ...idle, working: 1 })).toBe("Working");
    expect(summary({ ...idle, working: 3 })).toBe("Working — 3 conversations");
  });

  it("leads with you being needed", () => {
    expect(summary({ ...idle, working: 4, waiting: [{ label: "asked you" }] })).toBe(
      "Waiting on you",
    );
  });
});

describe("the next standing job", () => {
  const at = (minutes: number) => new Date(Date.now() + minutes * 60_000).toISOString();

  it("is the soonest active one", () => {
    const next = soonest([
      { status: "active", note: "later", next_fire: at(90) },
      { status: "active", note: "sooner", next_fire: at(10) },
    ]);
    expect(next?.note).toBe("sooner");
  });

  it("ignores the ones that are not running", () => {
    expect(soonest([{ status: "paused", note: "off", next_fire: at(1) }])).toBeNull();
  });

  it("ignores a date it cannot read rather than rendering NaN in the menu", () => {
    expect(soonest([{ status: "active", note: "broken", next_fire: "not a date" }])).toBeNull();
    expect(soonest([])).toBeNull();
  });

  it("counts down in the vocabulary the Work panel uses", () => {
    vi.useFakeTimers();
    try {
      const now = new Date("2026-08-23T12:00:00Z");
      vi.setSystemTime(now);
      expect(until("2026-08-23T12:12:00Z")).toBe("in 12m");
      expect(until("2026-08-23T15:30:00Z")).toBe("in 3h 30m");
      expect(until("2026-08-23T14:00:00Z")).toBe("in 2h");
      // Already past means the scheduler is about to run it, not that something went wrong.
      expect(until("2026-08-23T11:59:00Z")).toBe("due");
    } finally {
      vi.useRealTimers();
    }
  });
});
