import { ContextMeter } from "@/components/assistant-ui/context-meter";
import type { ContextLedger } from "@/lib/backend/types";
import { formatTokens, realTokens, sumUsage, usageTitle, type Usage } from "@/lib/tokens";

/** Every model request in this turn, in the order they finished. */
export interface TurnUsage {
  rounds: Usage[];
  /** The window as of the last round. Absent on a turn that predates the ledger. */
  context?: ContextLedger;
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
 */
export function TurnTokens({ usage }: { usage: TurnUsage }) {
  const rounds = usage.rounds ?? [];
  // The meter can have something to say on a turn that spent nothing worth reporting, so the
  // two are independent — but an empty footer should still render as nothing.
  const meter = usage.context ? (
    <ContextMeter context={usage.context} folded={usage.folded} />
  ) : null;
  if (rounds.length === 0) return meter;
  const total = rounds.reduce((sum, one) => sum + realTokens(one), 0);
  const each = rounds.map((one, i) => `${i + 1}. ${formatTokens(realTokens(one))}`).join("   ");
  return (
    <>
      {meter}
      <div
        data-slot="kith_turn-usage"
        // No top margin: it sits on the message's footer row now, beside the action bar,
        // rather than as a line of its own at the end of the body.
        className="text-muted-foreground/45 me-1 font-mono text-[10px] tabular-nums select-none"
        title={`${rounds.length} request${rounds.length === 1 ? "" : "s"} · ${usageTitle(sumUsage(rounds))}\n${each}`}
      >
        {formatTokens(total)} tokens
      </div>
    </>
  );
}
