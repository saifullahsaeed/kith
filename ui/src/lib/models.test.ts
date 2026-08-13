import { describe, expect, it } from "vitest";

import { RANKINGS, rank, turnCost, value } from "./models";
import type { ModelOption } from "./backend";

/** A catalogue entry with everything unknown, so each test states only what it is about. */
function model(over: Partial<ModelOption> = {}): ModelOption {
  return {
    id: "vendor/model",
    name: "Vendor: model",
    promptPerMTok: null,
    completionPerMTok: null,
    cacheReadPerMTok: null,
    cacheWritePerMTok: null,
    context: null,
    supportsTools: true,
    agenticIndex: null,
    codingIndex: null,
    intelligenceIndex: null,
    arena: null,
    description: "",
    knowledgeCutoff: "",
    releasedOn: null,
    maxOutput: null,
    webSearchPerCall: null,
    openWeights: false,
    retiresOn: null,
    inputModalities: ["text"],
    supportsImages: false,
    supportsFiles: false,
    supportsReasoning: false,
    ...over,
  };
}

/** The real shape, from OpenRouter's catalogue on the day this was written. */
const OPUS = model({
  id: "anthropic/claude-opus-5",
  promptPerMTok: 5,
  completionPerMTok: 25,
  cacheReadPerMTok: 0.5,
  codingIndex: 78,
  agenticIndex: 59.2,
  intelligenceIndex: 63.1,
});
const GROK = model({
  id: "x-ai/grok-4.6",
  promptPerMTok: 2,
  completionPerMTok: 6,
  cacheReadPerMTok: 0.5,
  codingIndex: 76.8,
  agenticIndex: 58.7,
  intelligenceIndex: 60.9,
});

describe("turnCost", () => {
  it("prices the prompt side at the cache rate, because every request is cached", () => {
    // (0.5 + 4×25) / 5 — not the $5 input price, which is the number that flatters
    // the dearest model in the catalogue.
    expect(turnCost(OPUS)).toBeCloseTo(20.1);
  });

  it("falls back to the input price when no cache rate is quoted", () => {
    expect(turnCost(model({ promptPerMTok: 5, completionPerMTok: 25 }))).toBeCloseTo(21);
  });

  it("is unknown when either half is", () => {
    expect(turnCost(model({ promptPerMTok: 5 }))).toBeNull();
    expect(turnCost(model({ completionPerMTok: 25 }))).toBeNull();
  });
});

describe("value", () => {
  it("is coding ability per dollar of that turn", () => {
    // Grok scores 1.2 points lower and costs a quarter as much to run.
    expect(value(GROK)! / value(OPUS)!).toBeGreaterThan(4);
  });

  it("is unknown for a model nobody measured, rather than zero", () => {
    expect(value(model({ promptPerMTok: 1, completionPerMTok: 1 }))).toBeNull();
  });
});

const by = (key: string) => RANKINGS.find((one) => one.key === key)!;

describe("rank", () => {
  it("orders each axis on its own number — that is the whole point of the control", () => {
    // A near-frontier coder at a twentieth of the price: top on value and on cost,
    // still second on raw coding ability. One sort cannot say all three.
    const bargain = model({
      id: "bargain",
      codingIndex: 70,
      promptPerMTok: 1,
      completionPerMTok: 1,
    });
    const ranked = (key: string) => rank([bargain, OPUS, GROK], by(key)).map((one) => one.id);

    expect(ranked("coding")[0]).toBe("anthropic/claude-opus-5");
    expect(ranked("value")[0]).toBe("bargain");
    expect(ranked("cheapest")[0]).toBe("bargain");
  });

  it("does not call a weak model good value just because it is cheap", () => {
    // Coding index 10 at $1/$1 scores 10 points per dollar; Grok scores 76.8 at $4.90
    // and beats it. Value is ability per dollar, not the absence of a price.
    const weakAndCheap = model({
      id: "weak",
      codingIndex: 10,
      promptPerMTok: 1,
      completionPerMTok: 1,
    });
    expect(rank([weakAndCheap, GROK], by("value")).map((one) => one.id)).toEqual([
      "x-ai/grok-4.6",
      "weak",
    ]);
  });

  it("puts what nobody measured last, never first", () => {
    // The trap in every one of these: an unscored model sorting above a measured one
    // reads as a recommendation the catalogue never made.
    const unknown = model({ id: "unknown" });
    const weak = model({ id: "weak", codingIndex: 1 });
    expect(rank([unknown, weak], by("coding")).map((one) => one.id)).toEqual(["weak", "unknown"]);
  });

  it("inverts the arena, where first place is the smallest number", () => {
    const first = model({
      id: "first",
      arena: { rank: 1, category: "models/3d", elo: 1, winRate: 1 },
    });
    const fortieth = model({
      id: "fortieth",
      arena: { rank: 40, category: "models/3d", elo: 1, winRate: 1 },
    });
    expect(rank([fortieth, first], by("arena")).map((one) => one.id)).toEqual([
      "first",
      "fortieth",
    ]);
  });

  it("keeps the provider's order within a tie, so the list does not reshuffle", () => {
    const a = model({ id: "a", codingIndex: 70 });
    const b = model({ id: "b", codingIndex: 70 });
    expect(rank([a, b], by("coding")).map((one) => one.id)).toEqual(["a", "b"]);
    expect(rank([b, a], by("coding")).map((one) => one.id)).toEqual(["b", "a"]);
  });

  it("never drops a model, whatever it is missing", () => {
    const pool = [OPUS, GROK, model({ id: "bare" })];
    for (const option of RANKINGS) {
      expect(rank(pool, option)).toHaveLength(3);
    }
  });
});
