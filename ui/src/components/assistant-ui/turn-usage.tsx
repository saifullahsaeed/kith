import type { ContextLedger } from "@/lib/backend/types";
import { formatTokens, realTokens, sumUsage, usageTitle, type Usage } from "@/lib/tokens";

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
  /** Whether this turn had to fold its middle into notes to keep going. */
  folded?: boolean;
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
export function TurnTokens({ usage }: { usage: TurnUsage }) {
  const rounds = usage.rounds ?? [];
  if (rounds.length === 0) return null;
  const total = rounds.reduce((sum, one) => sum + realTokens(one), 0);
  const each = rounds.map((one, i) => `${i + 1}. ${formatTokens(realTokens(one))}`).join("   ");
  return (
    <div
      data-slot="kith_turn-usage"
      // No top margin: it sits on the message's footer row now, beside the action bar,
      // rather than as a line of its own at the end of the body.
      className="text-muted-foreground/45 me-1 font-mono text-[10px] tabular-nums select-none"
      title={`${rounds.length} request${rounds.length === 1 ? "" : "s"} · ${usageTitle(sumUsage(rounds))}\n${each}`}
    >
      {formatTokens(total)} tokens
    </div>
  );
}
