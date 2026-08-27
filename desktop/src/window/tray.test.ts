import { describe, expect, it, vi } from "vitest";

import { ago, mark, moodOf, soonest, summary, took, turns, until, type State } from "./tray";

const idle: State = {
  working: 0,
  doing: "",
  waiting: [],
  recent: [],
  costUsd: 0,
  nextWake: null,
  update: null,
};

/**
 * The whole point of the menu bar is the glance.
 *
 * Before this the icon was one static template image whatever was happening — mid-turn, idle, or
 * stopped waiting on an answer it could not continue without — so the only way to find out was
 * to open the menu, and the menu was reading a deleted endpoint through an unauthenticated fetch
 * and therefore said "Kith" forever.
 */
describe("the three states", () => {
  it("is resting when he is neither running nor blocked", () => {
    expect(moodOf(idle)).toBe("resting");
  });

  it("is working while a turn is live", () => {
    expect(moodOf({ ...idle, working: 2 })).toBe("working");
  });

  it("is waiting the moment anything needs you", () => {
    expect(moodOf({ ...idle, waiting: [{ label: "a" }] })).toBe("waiting");
  });

  it("prefers waiting over working when both are true", () => {
    // Two conversations running and one of them stopped to ask: the question is the thing you
    // can act on, so it is the state the menu bar shows.
    expect(moodOf({ ...idle, working: 2, waiting: [{ label: "asked you" }] })).toBe("waiting");
  });
});

describe("the text beside the icon", () => {
  it("is nothing while resting or working — the icon says which", () => {
    // A `·` lived here and was indistinguishable from a dead pixel.
    expect(mark(idle)).toBe("");
    expect(mark({ ...idle, working: 3 })).toBe("");
  });

  it("is nothing for a single waiting thing, because the icon already changed", () => {
    expect(mark({ ...idle, waiting: [{ label: "a" }] })).toBe("");
  });

  it("counts only once there is more than one, which a shape cannot show", () => {
    expect(mark({ ...idle, waiting: [{ label: "a" }, { label: "b" }, { label: "c" }] })).toBe(" 3");
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

/**
 * What came of the time the window was shut.
 *
 * The question the menu could not answer at all, and the main reason it was not worth opening.
 * He runs while the window is closed — that is the whole premise of the tray existing — and
 * there was nowhere that said what came of it. "Here, nothing running" meant either "he has done
 * nothing all day" or "he finished everything an hour ago", and you could not tell which.
 */
describe("the last few turns", () => {
  const tick = (over: Record<string, unknown> = {}) => ({
    at: new Date(Date.now() - 5 * 60_000).toISOString(),
    focus: "fix the tray menu",
    seconds: 38,
    tools: ["read_file", "edit_file"],
    ...over,
  });

  it("keeps five at most — a menu bar is not a history", () => {
    expect(turns(Array.from({ length: 12 }, () => tick()))).toHaveLength(5);
  });

  it("drops a turn with nothing to show for it", () => {
    // A tick with no focus is one nobody asked for in words; naming it as a blank row would be
    // worse than leaving it out.
    expect(turns([tick({ focus: "   " }), tick({ at: 12345 })])).toEqual([]);
  });

  it("flattens a prompt that arrived over several lines", () => {
    const [one] = turns([tick({ focus: "look at\n\n  the loop  \n" })]);
    expect(one?.focus).toBe("look at the loop");
  });

  it("truncates rather than wrapping the menu", () => {
    const [one] = turns([tick({ focus: "x".repeat(200) })]);
    expect(one?.focus.length).toBeLessThanOrEqual(44);
    expect(one?.focus.endsWith("…")).toBe(true);
  });

  it("counts the tools rather than listing them", () => {
    expect(turns([tick()])[0]?.tools).toBe(2);
    expect(turns([tick({ tools: undefined })])[0]?.tools).toBe(0);
  });
});

describe("how long ago, and how long it took", () => {
  it("is coarse on purpose", () => {
    const at = (ms: number) => new Date(Date.now() - ms).toISOString();
    expect(ago(at(20_000))).toBe("just now");
    expect(ago(at(7 * 60_000))).toBe("7m ago");
    expect(ago(at(3 * 3_600_000))).toBe("3h ago");
    expect(ago(at(4 * 86_400_000))).toBe("4d ago");
  });

  it("says nothing rather than NaN for a date it cannot read", () => {
    expect(ago("not a date")).toBe("");
  });

  it("switches to minutes once seconds stop reading", () => {
    expect(took(38)).toBe("38s");
    expect(took(89)).toBe("89s");
    expect(took(240)).toBe("4m");
    expect(took(0)).toBe("");
  });
});

describe("the header, once there is something specific to say", () => {
  it("names the one thing he is on rather than saying 'working'", () => {
    expect(summary({ ...idle, working: 1, doing: "the tray menu" })).toBe(
      "Working on the tray menu",
    );
  });

  it("falls back to a count when several are running", () => {
    expect(summary({ ...idle, working: 3 })).toBe("Working — 3 conversations");
  });

  it("counts what is waiting, because one and five are different situations", () => {
    expect(summary({ ...idle, waiting: [{ label: "a" }] })).toBe("Waiting on you");
    expect(summary({ ...idle, waiting: [{ label: "a" }, { label: "b" }] })).toBe(
      "Waiting on you — 2 things",
    );
  });
});
