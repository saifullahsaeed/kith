/**
 * How one of his turns is laid out: a run strip of beats, then the answer.
 *
 * A turn is a loop — he says a sentence, calls tools, reads the results, thinks, says the next
 * sentence — and the thread used to draw every piece of that at the same weight, in arrival
 * order. On a real turn (median 18 calls, 10 reasoning blocks, 4 sentences) that is a wall of
 * apparatus with the answer somewhere at the bottom. The design turns it into two things:
 *
 *   * **the answer** — the last thing he said, drawn as prose, the hero of the turn;
 *   * **the run** — everything before it, folded into *beats*. A beat is one sentence he said
 *     mid-turn plus the work that followed it, in order. That is the unit a reader thinks in
 *     ("he checked the ARP table, then…"), and it is exactly how the model produced it.
 *
 * Pure and index-based, so it can be decided once per *shape* of the message (which parts,
 * of which kind, in which order) rather than once per streamed token — the shape only changes
 * when a new part arrives.
 */

/** What a part is, as far as the layout cares. */
export type PartKind =
  /** Prose he wrote: a beat's sentence, or the answer. */
  | "text"
  /** A reasoning block. */
  | "reasoning"
  /** A tool call. */
  | "tool"
  /** Something you said into the running turn, at the point it landed. */
  | "steer"
  /** Anything that draws nothing of its own here — the usage part, an unknown data part. */
  | "other";

export interface Beat {
  /** The sentence that opens the beat, or `null` for work done before he had said anything. */
  head: number | null;
  /** What sits under the sentence, in the order it should be read: reasoning, calls, steers. */
  items: number[];
  /** The tool calls among `items`, for counting. */
  calls: number[];
}

export interface TurnPlan {
  /** Whether the turn has any work worth a strip. A turn that only thought and answered does
   *  not: a strip saying "1 step" above a reply is apparatus with nothing in it. */
  strip: boolean;
  /** The run, beat by beat. Empty when there is no strip. */
  beats: Beat[];
  /** The answer: the last text part, provided nothing but inert parts follow it. `null` when
   *  the turn ended on work (it was stopped, or is still going) or there is no strip. */
  answer: number | null;
  /** When there is no strip, every drawable part in order — reasoning, prose, steers. */
  flat: number[];
}

/**
 * Lay out one turn.
 *
 * **Where reasoning goes.** His transcripts run `reasoning → sentence → call → reasoning →
 * sentence → call…`: the thinking comes *before* the sentence it produced. So a reasoning block
 * whose next substantive part is a sentence belongs to that sentence's beat — it is the thought
 * that led to it — and one followed by a call (or by nothing) stays in the beat it is in. Read
 * the other way, every beat would begin with a thought that belongs to the previous one, and the
 * first beat of nearly every turn would be a lone "thinking" row with no sentence at all.
 *
 * **The answer is decided the same way live and finished.** While he streams prose at the end
 * of a turn, it is drawn as the answer; if a call arrives after it, it becomes that beat's
 * sentence. Beat sentences are one-liners, so the move is small — and the alternative, drawing a
 * long streaming reply at 13px inside the strip until the turn ends, is worse every time.
 */
export function planTurn(kinds: readonly PartKind[], finished = false): TurnPlan {
  const drawable = (kind: PartKind | undefined) => kind !== undefined && kind !== "other";

  /* The answer.
   *
   * While he is working, only prose at the very end can be the answer — a sentence with a call
   * after it is narration. Once the turn is over, the answer is the last thing he *said*, even
   * with work after it: he routinely answers and then files a memory or a journal line, and
   * requiring the answer to be the last part put a whole multi-paragraph reply inside the strip,
   * drawn as a beat's "sentence", with the two bookkeeping calls under it. The work that follows
   * the answer is still shown — as the last beat of the strip, with no sentence of its own. */
  let answer: number | null = null;
  for (let i = kinds.length - 1; i >= 0; i--) {
    const kind = kinds[i];
    if (kind === "text") {
      answer = i;
      break;
    }
    if (kind === "other" || finished) continue;
    break;
  }

  const work = kinds.includes("tool");
  if (!work) {
    return {
      strip: false,
      beats: [],
      answer: null,
      flat: kinds.flatMap((kind, i) => (drawable(kind) ? [i] : [])),
    };
  }

  const end = kinds.length;
  const beats: Beat[] = [];
  let current: Beat | null = null;
  /** Reasoning waiting for the sentence it led to. */
  let pending: number[] = [];

  const open = (head: number | null): Beat => {
    const beat: Beat = { head, items: pending, calls: [] };
    pending = [];
    beats.push(beat);
    return beat;
  };

  for (let i = 0; i < end; i++) {
    const kind = kinds[i];
    if (!drawable(kind)) continue;

    // The answer is drawn after the strip, not in it. Anything after it starts a beat of its own
    // — reasoning that has no sentence to wait for, and calls — rather than joining the beat
    // before the answer, which would put post-answer work above the reply it came after.
    if (i === answer) {
      if (pending.length) {
        if (!current) current = open(null);
        else current.items.push(...pending);
        pending = [];
      }
      current = null;
      continue;
    }

    if (kind === "text") {
      current = open(i);
      continue;
    }

    if (kind === "reasoning") {
      let next = i + 1;
      while (next < end && (kinds[next] === "other" || kinds[next] === "reasoning")) next++;
      if (next < end && kinds[next] === "text" && next !== answer) {
        pending.push(i);
        continue;
      }
    }

    if (!current) current = open(null);
    current.items.push(i);
    if (kind === "tool") current.calls.push(i);
  }

  // Reasoning with no sentence after it before the answer — the thought he answered from.
  if (pending.length) {
    if (!current) current = open(null);
    else current.items.push(...pending);
    pending = [];
  }

  return { strip: true, beats, answer, flat: [] };
}

/** The layout's code for one message part, from the part itself. */
export function kindOf(
  part: { type: string; name?: string } | undefined,
  steerName: string,
): PartKind {
  if (!part) return "other";
  if (part.type === "text") return "text";
  if (part.type === "reasoning") return "reasoning";
  if (part.type === "tool-call") return "tool";
  if (part.type === "data" && part.name === steerName) return "steer";
  return "other";
}

/** One character per kind — how the shape is carried through a state selector as a string,
 *  which compares by value where an array would compare by identity. */
export const KIND_CODE: Record<PartKind, string> = {
  text: "t",
  reasoning: "r",
  tool: "c",
  steer: "s",
  other: "o",
};

const CODE_KIND: Record<string, PartKind> = Object.fromEntries(
  Object.entries(KIND_CODE).map(([kind, code]) => [code, kind as PartKind]),
);

export function kindsFromCode(code: string): PartKind[] {
  return [...code].map((c) => CODE_KIND[c] ?? "other");
}

/** "4m 12s", "38s", "1h 3m" — how long a turn worked, for its strip. */
export function formatWorked(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

/** "1:08", "12:30", "1:02:05" — the live clock on a turn that is still running. */
export function formatClock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const ss = String(s).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}
