import { describe, expect, it } from "vitest";

import { formatClock, formatWorked, kindsFromCode, planTurn } from "./turn-plan";

const plan = (code: string) => planTurn(kindsFromCode(code));

describe("planTurn", () => {
  it("draws a turn with no calls flat, with no strip", () => {
    // reasoning, then the answer, then the usage part
    expect(plan("rto")).toEqual({ strip: false, beats: [], answer: null, flat: [0, 1] });
  });

  it("makes one beat per sentence, and the last sentence the answer", () => {
    // said, call, call, said, call, answer, usage
    const out = plan("tcctcto");
    expect(out.strip).toBe(true);
    expect(out.answer).toBe(5);
    expect(out.beats).toEqual([
      { head: 0, items: [1, 2], calls: [1, 2] },
      { head: 3, items: [4], calls: [4] },
    ]);
  });

  it("gives reasoning to the sentence it led to, not the beat before it", () => {
    // the shape of his real transcripts: R T C R T C R answer
    const out = plan("rtcrtcrt");
    expect(out.answer).toBe(7);
    expect(out.beats).toEqual([
      { head: 1, items: [0, 2], calls: [2] },
      { head: 4, items: [3, 5, 6], calls: [5] },
    ]);
  });

  it("keeps reasoning between two calls inside the beat it is in", () => {
    const out = plan("tcrct");
    expect(out.beats).toEqual([{ head: 0, items: [1, 2, 3], calls: [1, 3] }]);
    expect(out.answer).toBe(4);
  });

  it("puts calls before the first sentence into a beat with no sentence", () => {
    const out = plan("ccrtct");
    expect(out.beats[0]).toEqual({ head: null, items: [0, 1], calls: [0, 1] });
    expect(out.beats[1]).toEqual({ head: 3, items: [2, 4], calls: [4] });
  });

  it("has no answer when the turn ended on work", () => {
    const out = plan("tcc");
    expect(out.answer).toBeNull();
    expect(out.beats).toEqual([{ head: 0, items: [1, 2], calls: [1, 2] }]);
  });

  it("places a steer where it landed", () => {
    const out = plan("tcsct");
    expect(out.beats).toEqual([{ head: 0, items: [1, 2, 3], calls: [1, 3] }]);
  });

  it("finds the answer of a finished turn even when he filed something after it", () => {
    // the shape that put a whole reply inside the strip: … sentence, call, ANSWER, call, call, thought
    const live = planTurn(kindsFromCode("tctccr"));
    expect(live.answer).toBeNull();
    const done = planTurn(kindsFromCode("tctccr"), true);
    expect(done.answer).toBe(2);
    expect(done.beats).toEqual([
      { head: 0, items: [1], calls: [1] },
      { head: null, items: [3, 4, 5], calls: [3, 4] },
    ]);
  });

  it("keeps the thought that led to the answer out of the post-answer beat", () => {
    // sentence, call, thought, ANSWER, call
    const done = planTurn(kindsFromCode("tcrtc"), true);
    expect(done.answer).toBe(3);
    expect(done.beats).toEqual([
      { head: 0, items: [1, 2], calls: [1] },
      { head: null, items: [4], calls: [4] },
    ]);
  });

  it("keeps a thought after the last call, before the answer, in the last beat", () => {
    const out = plan("tcrrt");
    expect(out.beats).toEqual([{ head: 0, items: [1, 2, 3], calls: [1] }]);
    expect(out.answer).toBe(4);
  });
});

describe("durations", () => {
  it("says how long a turn worked", () => {
    expect(formatWorked(38_000)).toBe("38s");
    expect(formatWorked(252_000)).toBe("4m 12s");
    expect(formatWorked(3_780_000)).toBe("1h 3m");
  });

  it("runs a clock for a live turn", () => {
    expect(formatClock(68_000)).toBe("1:08");
    expect(formatClock(3_725_000)).toBe("1:02:05");
  });
});
