/**
 * Ranking a catalogue of four hundred models, so nobody has to go and read about them
 * somewhere else.
 *
 * The picker used to sort on one axis — agentic ability — because that is what Kith
 * does, and stop there. Which is right for "what should he think with" and useless for
 * the question people actually arrive with, which is "what is worth trying today". Those
 * are different orders: on the current catalogue the best coder is fourth on agentic
 * ability, the best agentic model is fifth on coding, and the best value on either is in
 * neither top ten. One sort cannot answer all three, so the axis is a control.
 *
 * Every number here comes from the provider — Artificial Analysis' three indices and
 * Design Arena's head-to-head ranks, both carried in OpenRouter's catalogue — except
 * `value`, which is arithmetic on prices they publish and is defined in full below. None
 * of it is a judgement of ours dressed as data.
 */

import type { ModelOption } from "@/lib/backend";

/**
 * What a million tokens of work actually costs, for the mix Kith runs.
 *
 * Not the input price, which is the number everyone quotes and the least true: a turn
 * bills output at four to five times input, and every request Kith sends is cached, so
 * most of a warm round's prompt is billed at the read rate rather than the input rate.
 * Ranking on the input price alone puts the dearest model in the catalogue at the top of
 * a "cheapest" list.
 *
 * So: the prompt side is priced at the cache-read rate where the provider quotes one,
 * and the two sides are blended 1:4 to match how a turn actually bills. It is an
 * estimate and it is stated as one — but it is the same estimate for every row, which is
 * all a ranking needs.
 */
export function turnCost(model: ModelOption): number | null {
  const out = model.completionPerMTok;
  const inp = model.cacheReadPerMTok ?? model.promptPerMTok;
  if (out === null || inp === null) return null;
  const blended = (inp + 4 * out) / 5;
  return blended > 0 ? blended : null;
}

/** Coding ability per dollar of that turn — "most valuable coder", as a number. */
export function value(model: ModelOption): number | null {
  const cost = turnCost(model);
  if (cost === null || model.codingIndex === null) return null;
  return model.codingIndex / cost;
}

/** One way to order the catalogue, and the column that proves the order. */
export interface Ranking {
  key: string;
  label: string;
  /** What the ranking is *for*, in a line. Shown under the table rather than in a
   *  tooltip: the reason to pick this axis is not something you can hover to discover. */
  hint: string;
  /** How to sort. Higher is better, and null sorts last — a model with no measurement
   *  must not outrank one that was measured and scored badly. */
  score: (model: ModelOption) => number | null;
  /** The third numeric column, which follows the sort so the order is always visible.
   *  Coding and agentic have columns of their own and never move. */
  column: { label: string; of: (model: ModelOption) => string };
}

const INTELLIGENCE = {
  label: "intel",
  of: (m: ModelOption) => (m.intelligenceIndex === null ? "—" : m.intelligenceIndex.toFixed(1)),
};

export const RANKINGS: Ranking[] = [
  {
    key: "coding",
    label: "Coding",
    hint: "Artificial Analysis' coding index. The one to sort by when the question is what to write code with.",
    score: (m) => m.codingIndex,
    column: INTELLIGENCE,
  },
  {
    key: "agentic",
    label: "Agentic",
    hint: "How well it sustains multi-step tool use — which is the whole of what Kith does, whatever else it is good at.",
    score: (m) => m.agenticIndex,
    column: INTELLIGENCE,
  },
  {
    key: "intelligence",
    label: "Intelligence",
    hint: "The general index, across reasoning, maths and knowledge rather than code alone.",
    score: (m) => m.intelligenceIndex,
    column: INTELLIGENCE,
  },
  {
    key: "value",
    label: "Value",
    hint: "Coding index per dollar, priced on a cached 1:4 in/out turn — the best coder you are not overpaying for.",
    score: value,
    column: {
      label: "pts/$",
      of: (m) => {
        const points = value(m);
        return points === null
          ? "—"
          : points < 10
            ? points.toFixed(1)
            : Math.round(points).toString();
      },
    },
  },
  {
    key: "cheapest",
    label: "Cheapest",
    hint: "By what a turn costs rather than by the input price, which is the number that flatters the dearest models.",
    score: (m) => {
      const cost = turnCost(m);
      return cost === null ? null : -cost;
    },
    column: INTELLIGENCE,
  },
  {
    key: "arena",
    label: "Arena",
    hint: "Design Arena: head-to-head battles judged by people, not an index. Best placing across every category.",
    // Rank 1 is best, so it inverts — and a model with no placing must sort last
    // rather than first, which is what the raw number would do.
    score: (m) => (m.arena ? -m.arena.rank : null),
    column: {
      label: "arena",
      of: (m) => (m.arena ? `#${m.arena.rank}` : "—"),
    },
  },
  {
    key: "context",
    label: "Context",
    hint: "How much he can hold at once. Past a point this stops mattering; below it nothing else does.",
    score: (m) => m.context,
    column: { label: "ctx", of: (m) => (m.context ? `${Math.round(m.context / 1000)}K` : "—") },
  },
  {
    key: "new",
    label: "Newest",
    hint: "Release date. The honest answer to “what should I try today” when everything else is a tie.",
    score: (m) => (m.releasedOn ? Date.parse(m.releasedOn) : null),
    column: { label: "released", of: (m) => (m.releasedOn ? m.releasedOn.slice(0, 7) : "—") },
  },
];

/**
 * Order the catalogue, unmeasured models last.
 *
 * Stable within a tie on the provider's own order, which is already ranked — so two
 * models on the same score keep the ordering the provider gave them rather than an
 * arbitrary one, and the list does not reshuffle under you between renders.
 */
export function rank(models: ModelOption[], by: Ranking): ModelOption[] {
  return models
    .map((model, index) => ({ model, index, score: by.score(model) }))
    .sort((a, b) => {
      if (a.score === null && b.score === null) return a.index - b.index;
      if (a.score === null) return 1;
      if (b.score === null) return -1;
      return b.score - a.score || a.index - b.index;
    })
    .map((entry) => entry.model);
}

/* ── Filters ──────────────────────────────────────────────────────────────
 *
 * Separate from `RANKINGS`, and the distinction is the point. A ranking answers "best by which
 * measure" and there can only be one at a time; a filter answers "must have this" and they
 * combine. The picker offered only the first, so "a cheap model that takes images and does tool
 * calls" was a question you answered by reading 361 rows.
 *
 * Every one of these reads a field the catalogue already carries and the panel already showed
 * one model at a time — so this is not new data, it is the same data asked as a question.
 * `null` means the provider did not say, and an unknown is never treated as a yes: a filter for
 * tool support must not hand back a model that merely failed to mention it. */

export interface ModelFilter {
  key: string;
  label: string;
  /** What it means, and why you would want it. Shown on hover. */
  hint: string;
  /** Grouped in the bar so mutually-informative ones sit together. */
  group: "can" | "shape" | "cost";
  matches: (model: ModelOption) => boolean;
}

/** Context windows worth drawing a line at, in tokens. */
const BIG_CONTEXT = 200_000;
/** Dollars per million input tokens, below which a model is cheap to run all day. */
const CHEAP_IN = 1;

export const FILTERS: ModelFilter[] = [
  {
    key: "tools",
    label: "Tools",
    hint: "Calls tools. Kith is a loop around tool calls, so a model without this cannot do the job at all — unknown does not count as yes.",
    group: "can",
    matches: (m) => m.supportsTools === true,
  },
  {
    key: "images",
    label: "Images",
    hint: "Takes pictures as input — screenshots, diagrams, a photo of a whiteboard.",
    group: "can",
    matches: (m) => m.supportsImages,
  },
  {
    key: "files",
    label: "Files",
    hint: "Takes files directly, rather than needing their text pasted in.",
    group: "can",
    matches: (m) => m.supportsFiles,
  },
  {
    key: "reasoning",
    label: "Reasoning",
    hint: "Has a thinking budget an effort setting can actually move. On anything else the Thinking control means nothing.",
    group: "can",
    matches: (m) => m.supportsReasoning,
  },
  {
    key: "open",
    label: "Open weights",
    hint: "The weights are published, so the model can outlive whoever is serving it today.",
    group: "shape",
    matches: (m) => m.openWeights,
  },
  {
    key: "big-context",
    label: "200K+ context",
    hint: "A window big enough to hold a long conversation without folding it into notes every turn.",
    group: "shape",
    matches: (m) => (m.context ?? 0) >= BIG_CONTEXT,
  },
  {
    key: "staying",
    label: "Not retiring",
    hint: "No withdrawal date announced. A model with one will stop answering on a day already in the calendar.",
    group: "shape",
    matches: (m) => !m.retiresOn,
  },
  {
    key: "cheap",
    label: "Under $1/M in",
    hint: "Cheap enough to leave running. Input price is the one that matters here, because a turn re-sends its whole history every round.",
    group: "cost",
    matches: (m) => m.promptPerMTok !== null && m.promptPerMTok < CHEAP_IN,
  },
  {
    key: "cached",
    label: "Quotes caching",
    hint: "Publishes a cached-read price. Kith caches every request, so on a warm round most of the prompt bills at that rate — usually a tenth of input.",
    group: "cost",
    matches: (m) => m.cacheReadPerMTok !== null,
  },
];

/** Everything that passes every chosen filter. AND, not OR: each one you add is another
 *  requirement, which is what makes them worth chaining. */
export function applyFilters(models: ModelOption[], chosen: ReadonlySet<string>): ModelOption[] {
  if (chosen.size === 0) return models;
  const active = FILTERS.filter((one) => chosen.has(one.key));
  return models.filter((model) => active.every((one) => one.matches(model)));
}

/** What a model can be given and what it can do, as short words for a row. */
export function capabilitiesOf(model: ModelOption): string[] {
  const out: string[] = [];
  if (model.supportsTools === true) out.push("tools");
  if (model.supportsImages) out.push("images");
  if (model.supportsFiles) out.push("files");
  if (model.supportsReasoning) out.push("reasoning");
  if (model.openWeights) out.push("open");
  return out;
}
