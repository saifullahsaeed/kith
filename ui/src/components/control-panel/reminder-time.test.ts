import { describe, expect, it } from "vitest";

import {
  agoPhrase,
  cadenceBadge,
  cadencePreview,
  countdown,
  dayAt,
  firePayload,
  fromPicked,
  minutesUntil,
  parseClock,
  resolveQuickTime,
  setAhead,
  snoozeTarget,
  timeSpan,
  untilLong,
  weekStrip,
} from "./reminder-time";

// Local times throughout, so these hold in whatever zone the suite runs in.
const at = (y: number, mo: number, d: number, h = 0, mi = 0) => new Date(y, mo - 1, d, h, mi);
const NOW = at(2026, 9, 30, 15, 0); // Wed 30 Sep 2026, 3:00 PM

describe("resolveQuickTime", () => {
  it("adds minutes for the relative choices", () => {
    expect(resolveQuickTime("30m", NOW)).toEqual(at(2026, 9, 30, 15, 30));
    expect(resolveQuickTime("2h", NOW)).toEqual(at(2026, 9, 30, 17, 0));
  });

  it("puts Tonight at 9 PM today, and tomorrow night once nine has passed", () => {
    expect(resolveQuickTime("tonight", NOW)).toEqual(at(2026, 9, 30, 21, 0));
    expect(resolveQuickTime("tonight", at(2026, 9, 30, 22, 15))).toEqual(at(2026, 10, 1, 21, 0));
  });

  it("puts Tomorrow at nine the next morning, across a month end", () => {
    expect(resolveQuickTime("tomorrow", NOW)).toEqual(at(2026, 10, 1, 9, 0));
  });

  it("reads a picked day and time, and refuses half of one", () => {
    expect(resolveQuickTime("pick", NOW, { date: "2026-10-02", time: "14:30" })).toEqual(at(2026, 10, 2, 14, 30));
    expect(resolveQuickTime("pick", NOW, { date: "2026-10-02", time: "nope" })).toBeNull();
    expect(resolveQuickTime("pick", NOW)).toBeNull();
    expect(fromPicked({ date: "", time: "09:00" })).toBeNull();
  });
});

describe("firePayload", () => {
  it("sends relative choices as minutes and absolute ones as an instant", () => {
    expect(firePayload("30m", at(2026, 9, 30, 15, 30))).toEqual({ in_minutes: 30 });
    expect(firePayload("2h", at(2026, 9, 30, 17, 0))).toEqual({ in_minutes: 120 });
    const tonight = at(2026, 9, 30, 21, 0);
    expect(firePayload("tonight", tonight)).toEqual({ at: tonight.toISOString() });
  });
});

describe("minutesUntil", () => {
  it("rounds to whole minutes and never goes below one", () => {
    expect(minutesUntil(at(2026, 10, 1, 9, 0), NOW)).toBe(18 * 60);
    expect(minutesUntil(NOW, NOW)).toBe(1);
  });
});

describe("snoozeTarget", () => {
  it("moves a future reminder later than its own time, not later than now", () => {
    expect(snoozeTarget(at(2026, 9, 30, 15, 40), NOW, 30)).toEqual(at(2026, 9, 30, 16, 10));
  });
  it("moves an overdue one from now", () => {
    expect(snoozeTarget(at(2026, 9, 30, 14, 0), NOW, 30)).toEqual(at(2026, 9, 30, 15, 30));
  });
});

describe("saying when", () => {
  it("names the day relative to today", () => {
    expect(dayAt(at(2026, 9, 30, 15, 30), NOW)).toBe("today at 3:30 PM");
    expect(dayAt(at(2026, 10, 1, 9, 0), NOW)).toBe("tomorrow at 9:00 AM");
    expect(dayAt(at(2026, 10, 2, 9, 0), NOW)).toBe("Fri 2 Oct at 9:00 AM");
  });

  it("says how long until, in words", () => {
    expect(untilLong(at(2026, 9, 30, 15, 30), NOW)).toBe("in 30 minutes");
    expect(untilLong(at(2026, 9, 30, 17, 0), NOW)).toBe("in 2 hours");
    expect(untilLong(at(2026, 9, 30, 16, 20), NOW)).toBe("in 1 hour 20 minutes");
    expect(untilLong(at(2026, 10, 3, 15, 0), NOW)).toBe("in 3 days");
  });

  it("counts down compactly, and says when one is late", () => {
    expect(countdown(at(2026, 9, 30, 15, 30), NOW)).toBe("in 30 min");
    expect(countdown(at(2026, 9, 30, 17, 0), NOW)).toBe("in 2 h");
    expect(countdown(at(2026, 9, 30, 16, 5), NOW)).toBe("in 1 h 5 min");
    expect(countdown(NOW, NOW)).toBe("now");
    expect(countdown(at(2026, 9, 30, 13, 0), NOW)).toBe("2 h late");
  });

  it("says how long ago, then the date", () => {
    expect(agoPhrase(NOW, NOW)).toBe("just now");
    expect(agoPhrase(at(2026, 9, 30, 14, 48), NOW)).toBe("12 min ago");
    expect(agoPhrase(at(2026, 9, 30, 11, 0), NOW)).toBe("4 h ago");
    expect(agoPhrase(at(2026, 9, 29, 23, 0), NOW)).toBe("yesterday");
    expect(agoPhrase(at(2026, 8, 12, 9, 0), NOW)).toBe("Aug 12");
  });

  it("says how far ahead a reminder was set", () => {
    expect(setAhead(at(2026, 8, 12, 22, 28), at(2026, 8, 12, 22, 35))).toBe("Set 7 min ahead");
  });

  it("writes a span the way people write one", () => {
    expect(timeSpan(at(2026, 8, 12, 20, 47), at(2026, 8, 12, 22, 35))).toBe("8:47 – 10:35 PM");
    expect(timeSpan(at(2026, 8, 6, 7, 48), at(2026, 8, 6, 15, 1))).toBe("7:48 AM – 3:01 PM");
    expect(timeSpan(at(2026, 8, 30, 9, 30), at(2026, 8, 30, 9, 30))).toBe("9:30 AM");
  });
});

describe("parseClock", () => {
  it("takes the ways a time gets typed", () => {
    expect(parseClock("09:00")).toEqual({ hour: 9, minute: 0 });
    expect(parseClock("9")).toEqual({ hour: 9, minute: 0 });
    expect(parseClock("930")).toEqual({ hour: 9, minute: 30 });
    expect(parseClock("9:30 pm")).toEqual({ hour: 21, minute: 30 });
    expect(parseClock("12am")).toEqual({ hour: 0, minute: 0 });
    expect(parseClock("21:30")).toEqual({ hour: 21, minute: 30 });
  });
  it("refuses what is not a time", () => {
    expect(parseClock("25:00")).toBeNull();
    expect(parseClock("9:75")).toBeNull();
    expect(parseClock("13pm")).toBeNull();
    expect(parseClock("soon")).toBeNull();
  });
});

describe("cadences", () => {
  it("reads a cadence on its badge", () => {
    expect(cadenceBadge({ daily_at: "09:00", every_minutes: null })).toBe("Every day · 09:00");
    expect(cadenceBadge({ daily_at: null, every_minutes: 60 })).toBe("Every hour");
    expect(cadenceBadge({ daily_at: null, every_minutes: 90 })).toBe("Every 1 h 30 min");
    expect(cadenceBadge({ daily_at: null, every_minutes: 30 })).toBe("Every 30 min");
  });

  it("previews the first run the way the server will schedule it", () => {
    expect(cadencePreview({ kind: "daily", at: "09:00" }, NOW)).toEqual({
      cadence: "every day at 9:00 AM",
      first: "first run tomorrow, Thu 1 Oct",
    });
    expect(cadencePreview({ kind: "daily", at: "18:00" }, NOW)).toEqual({
      cadence: "every day at 6:00 PM",
      first: "first run today, 6:00 PM",
    });
    expect(cadencePreview({ kind: "every", minutes: 45 }, NOW)?.first).toBe("first run today at 3:45 PM");
    expect(cadencePreview({ kind: "daily", at: "late" }, NOW)).toBeNull();
    expect(cadencePreview({ kind: "every", minutes: 0 }, NOW)).toBeNull();
  });

  it("lays out the next seven days of a daily run, starting from the next one", () => {
    const strip = weekStrip("09:00", at(2026, 10, 1, 9, 0), NOW, false);
    expect(strip.map((day) => `${day.dow} ${day.date}`)).toEqual([
      "Thu 1",
      "Fri 2",
      "Sat 3",
      "Sun 4",
      "Mon 5",
      "Tue 6",
      "Wed 7",
    ]);
    expect(strip.filter((day) => day.run)).toHaveLength(7);
    expect(strip.filter((day) => day.next).map((day) => day.date)).toEqual([1]);
    expect(weekStrip("09:00", at(2026, 10, 1, 9, 0), NOW, true).some((day) => day.run)).toBe(false);
  });
});
