import { USAGE_PART } from "@/lib/backend/adapter";
import type { ContextLedger } from "@/lib/backend/types";
import { formatTokens, type Usage } from "@/lib/tokens";
import { cn } from "@/lib/utils";

import { Spin } from "./spin";

/** Every model request in this turn, in the order they finished. */
export interface TurnUsage {
  rounds: Usage[];
  /** The window as of the last round — this turn's own peak, after whatever it did with
   *  tools. Bounces turn to turn with how much work each one happened to do; kept for the
   *  tooltip breakdown, not for the headline number. Absent on a turn that predates the ledger. */
  context?: ContextLedger;
  /** The window as of round one — before this turn's own tool calls added anything. What was
   *  actually carried over from before, so it climbs with the conversation itself instead of
   *  with any one turn's tool use, and is what the meter shows. Absent on a turn recorded
   *  before this field existed; falls back to `context`. */
  baseline?: ContextLedger;
  /** Whether this turn had to fold to keep going. */
  folded?: boolean;
  /** How much prose the fold removed, when the fold happened before the turn started.
   *  That one is the whole perceived wait on a long conversation — a summarisation call
   *  big enough to be measured in millions of characters — so it is worth naming rather
   *  than leaving as an unexplained pause. */
  foldedChars?: { from: number; to: number };
  /** Set while a failed round is waiting to be tried again. A dropped connection used to
   *  end the whole turn; it now costs a pause, and a pause with nothing in it looks exactly
   *  like the hang it is recovering from. */
  retrying?: { attempt: number; message: string };
  /** Set while the window is getting back to this turn after its own connection dropped — the
   *  laptop woke, or the browser restarted its network service. Not the turn's trouble: it is
   *  still running on the server, and this says so rather than "Didn't finish". */
  rejoining?: { attempt: number };
  /** A tool call whose arguments are still streaming. A `write_file` of a page arrives over
   *  something like a minute, and until now that minute showed nothing at all — so watching a
   *  file be written was indistinguishable from watching a turn hang. Present only while it is
   *  happening; the completed tool row that replaces it says the same thing with a result. */
  writing?: { name: string; path?: string; chars: number };
  /** How many rounds this turn had to send again, counted for the whole turn and never
   *  cleared. `retrying` above is live and the answer replaces it — which is fine when a
   *  failure is slow, and useless when it is not: a dead network fails name resolution in
   *  hundredths of a second, so the status line comes and goes inside three seconds and a
   *  turn that fought its way through six attempts finishes looking exactly like one that
   *  sailed. This is the part that stays, in the footer with the rest of what the turn cost. */
  retried?: number;
  /** What the turn cost, summed over its rounds, in dollars. Absent when no round was priced —
   *  a local model, a provider that reports nothing, or a turn read back from the transcript,
   *  whose timeline does not carry the price. Absent is "unknown", never "$0". */
  costUsd?: number;
  /** Which model answered the last round, as the provider named it ("deepseek/deepseek-v4.1-flash"). */
  model?: string;
  /** Which host served it ("Novita", "Together"). */
  provider?: string;
}

/** The most recent assistant turn's usage data part, walking back from the end — there is at most one per turn (see `USAGE_PART`), and an
 * in-progress turn's is exactly as current as the round that just landed.
 *
 * Exported because the Work panel reads the same figure. One reader rather than two: this walks
 * the thread from the end looking for one part shape, and a second copy of that in the panel is a
 * second thing to get wrong when the shape changes. */
export function latestUsage(messages: readonly unknown[]): TurnUsage | undefined {
  for (let i = messages.length - 1; i >= 0; i--) {
    const message = messages[i] as { role?: string; content?: unknown[] };
    if (message.role !== "assistant") continue;
    for (const part of message.content ?? []) {
      const candidate = part as { type?: string; name?: string; data?: TurnUsage };
      if (candidate.type === "data" && candidate.name === USAGE_PART && candidate.data) {
        return candidate.data;
      }
    }
  }
  return undefined;
}

/** A price the way a person reads one: "$0.19", "$0.004", "$1.24". */
export function formatCost(usd: number): string {
  if (usd >= 1) return `$${usd.toFixed(2)}`;
  if (usd >= 0.01) return `$${usd.toFixed(2)}`;
  if (usd > 0) return `$${usd.toFixed(3)}`;
  return "$0";
}

/** The model's own name, without the vendor prefix a router puts in front of it. */
export function shortModelName(id: string): string {
  const slash = id.lastIndexOf("/");
  return slash === -1 ? id : id.slice(slash + 1);
}

/**
 * The line under a finished answer: "$0.19 · deepseek-v4.1-flash via Novita".
 *
 * Each clause only when it is known — never borrowing today's model for a turn that may have run
 * on another. The token accounting (what was sent, how much came from cache) is on the tooltip;
 * how full the window is lives once, on the composer's ring.
 */
export function TurnFooterStats({ usage, title }: { usage: TurnUsage; title?: string }) {
  const rounds = usage.rounds ?? [];
  const sent = rounds.reduce((sum, one) => sum + one.uncached + one.cached, 0);
  const cached = rounds.reduce((sum, one) => sum + one.cached, 0);
  const clauses: string[] = [];
  if (usage.costUsd !== undefined) clauses.push(formatCost(usage.costUsd));
  // No token count here. Every answer said "412k in, 71% cached", so a long chat printed the
  // size of the window fifteen times down the page; the ring on the composer says it once, and
  // the tooltip keeps the accounting for whoever wants it.
  if (usage.model) {
    clauses.push(`${shortModelName(usage.model)}${usage.provider ? ` via ${usage.provider}` : ""}`);
  }
  const accounting = sent
    ? `${formatTokens(sent)} prompt tokens over ${rounds.length} request${rounds.length === 1 ? "" : "s"}, ` +
      `${formatTokens(cached)} of them from cache`
    : "";
  const tooltip = [title, accounting].filter(Boolean).join("\n");
  const retried = usage.retried ?? 0;
  if (!clauses.length && !retried) return null;
  return (
    <span
      data-slot="kith_turn-usage"
      className="text-muted-foreground/75 flex min-w-0 items-center gap-2 ps-1.5 text-[11px] tabular-nums select-none"
      title={tooltip || undefined}
    >
      {clauses.length ? <span className="truncate">{clauses.join(" · ")}</span> : null}
      {retried ? (
        <span
          className="text-kith/80 shrink-0"
          title={
            `A round failed and was sent again ${retried} time${retried === 1 ? "" : "s"}. ` +
            "The tools of the rounds before it had already run, so only the model request " +
            "repeated — nothing was done twice."
          }
        >
          sent again ×{retried}
        </span>
      ) : null}
    </span>
  );
}

/**
 * What this turn has cost so far, at the foot of the message.
 *
 * It counts up: a turn is a loop, and each round reports in as it lands, so the figure
 * climbs while he works and settles when he stops. That makes it a progress signal as
 * well as a total — a number still moving means he is still going.
 *
 * This started as a separate line under every round, which is more information and worse:
 * a six-round turn became six numbers threaded through the reply, competing with the
 * writing for attention. One running total at the bottom is what a person actually wants,
 * and the per-round split is on hover for when they don't.
 *
 * The context meter used to live here too, one per message — which meant it vanished the
 * moment you stopped sending messages, right when "how full am I" is worth being able to
 * check. It now lives once, under the composer — see `ComposerMeter` in thread.tsx — so
 * this only reports what this specific turn actually cost.
 */
/**
 * What the turn is doing right now, when it is doing something other than talking.
 *
 * Rendered inline with the message rather than in the footer beside the token count, and the
 * distinction is not cosmetic: the footer is right-aligned metadata about what a finished turn
 * cost. A live status put there ends up a thousand pixels from the working indicator it is
 * explaining, so the two read as unrelated — a lone pulsing dot on the left and an orphaned
 * sentence on the right. It belongs next to the thing it is about.
 */
export function TurnStatus({ usage }: { usage: TurnUsage }) {
  /* One shape for all three, as the design draws its "live lines": a small bordered pill with a
   * spinner, the state in its colour and the detail after a dash. Amber for a reconnect (it needs
   * nothing from you, but it is the one that can fail), blue for making room, green for writing —
   * green is "he is working right now", and writing a file is exactly that. */
  const pill = "flex w-fit max-w-full items-center gap-2 rounded-lg border px-3 py-1.5 text-xs select-none";
  if (usage.rejoining) {
    return (
      <div
        data-slot="kith_turn-status"
        className={cn(pill, "border-kith/30 bg-kith-soft/50")}
        title="This window lost its connection — the laptop slept, or the network changed. The turn kept running on the server, and the window is catching up with it."
      >
        <Spin tone="kith" className="size-3" />
        <span className="text-kith font-semibold">Reconnecting</span>
        <span className="text-muted-foreground truncate">— he is still working</span>
      </div>
    );
  }
  if (usage.retrying) {
    return (
      <div
        data-slot="kith_turn-status"
        className={cn(pill, "border-kith/30 bg-kith-soft/50")}
        title={`${usage.retrying.message}\n\nThe round is being sent again. His earlier rounds are kept either way — if it keeps failing he will write down what he found rather than lose it.`}
      >
        <Spin tone="kith" className="size-3" />
        <span className="text-kith font-semibold">Reconnecting</span>
        <span className="text-muted-foreground truncate">— attempt {usage.retrying.attempt + 1}</span>
      </div>
    );
  }
  if (usage.writing) {
    const { name, path, chars } = usage.writing;
    // The filename when the half-written JSON has given one up, the tool's own name otherwise.
    // "writing wukong-site/index.html" is a different sentence from "writing".
    const what = path || name || "a file";
    return (
      <div
        data-slot="kith_turn-status"
        className={cn(pill, "border-roam/30 bg-roam-soft/40")}
        title={
          "He is writing this out now. Large files arrive a piece at a time, and the count is " +
          "how far through it he is — the finished call reports the real size."
        }
      >
        <Spin className="size-3" />
        <span className="text-roam font-semibold">Writing</span>
        <code className="text-foreground/90 truncate font-mono">{what}</code>
        <span className="text-muted-foreground shrink-0">
          — {chars < 1000 ? `${chars}` : `${(chars / 1000).toFixed(1)}k`} chars
        </span>
      </div>
    );
  }
  // Only while nothing else has arrived. Once he is talking, the fold is over and saying so
  // would be a stale line sitting above a live answer.
  if (usage.folded && (usage.rounds ?? []).length === 0) {
    const removed = usage.foldedChars
      ? ` — summarising ≈${formatTokens(Math.round((usage.foldedChars.from - usage.foldedChars.to) / 4))} tokens`
      : "";
    return (
      <div
        data-slot="kith_turn-status"
        className={cn(pill, "border-blue-400/30 bg-blue-400/10")}
        title={
          "This conversation is past what the model can hold, so he is summarising the older " +
          "part before answering. It costs a model call, which is why it takes a moment."
        }
      >
        <Spin tone="blue" className="size-3" />
        <span className="font-semibold text-blue-400">Folding older turns</span>
        {removed ? <span className="text-muted-foreground truncate">{removed}</span> : null}
      </div>
    );
  }
  return null;
}
