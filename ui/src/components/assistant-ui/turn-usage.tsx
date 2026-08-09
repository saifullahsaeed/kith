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
  /** How many rounds this turn had to send again, counted for the whole turn and never
   *  cleared. `retrying` above is live and the answer replaces it — which is fine when a
   *  failure is slow, and useless when it is not: a dead network fails name resolution in
   *  hundredths of a second, so the status line comes and goes inside three seconds and a
   *  turn that fought its way through six attempts finishes looking exactly like one that
   *  sailed. This is the part that stays, in the footer with the rest of what the turn cost. */
  retried?: number;
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
  if (usage.retrying) {
    return (
      <div
        data-slot="kith_turn-status"
        className="flex items-center gap-1.5 font-mono text-[11px] text-amber-600/80 select-none dark:text-amber-400/80"
        title={`${usage.retrying.message}\n\nThe round is being sent again. His earlier rounds are kept either way — if it keeps failing he will write down what he found rather than lose it.`}
      >
        <span className="size-1.5 animate-pulse rounded-full bg-amber-500/60" />
        reconnecting — attempt {usage.retrying.attempt + 1}
      </div>
    );
  }
  // Only while nothing else has arrived. Once he is talking, the fold is over and saying so
  // would be a stale line sitting above a live answer.
  if (usage.folded && (usage.rounds ?? []).length === 0) {
    const removed = usage.foldedChars
      ? ` — summarising ${formatTokens(Math.round((usage.foldedChars.from - usage.foldedChars.to) / 4))}`
      : "";
    return (
      <div
        data-slot="kith_turn-status"
        className="text-muted-foreground/70 flex items-center gap-1.5 font-mono text-[11px] select-none"
        title={
          "This conversation is past what the model can hold, so he is summarising the older " +
          "part before answering. It costs a model call, which is why it takes a moment."
        }
      >
        <span className="bg-muted-foreground/50 size-1.5 animate-pulse rounded-full" />
        making room{removed}
      </div>
    );
  }
  return null;
}


export function TurnTokens({ usage }: { usage: TurnUsage }) {
  const rounds = usage.rounds ?? [];
  const retried = usage.retried ?? 0;
  // The footer is what the turn *cost*, and a turn with no rounds has cost nothing yet. What
  // it is doing meanwhile is `TurnStatus`, inline with the message.
  //
  // Retries are the exception to "cost nothing yet", and the exception matters: the turn most
  // worth telling someone about is the one where the first round never landed, which has no
  // rounds at all. Gating the whole footer on `rounds` would have hidden the marker in exactly
  // the case it was added for.
  if (rounds.length === 0 && !retried) return null;
  const total = rounds.reduce((sum, one) => sum + realTokens(one), 0);
  const each = rounds.map((one, i) => `${i + 1}. ${formatTokens(realTokens(one))}`).join("   ");
  return (
    <div
      data-slot="kith_turn-usage"
      // No top margin: it sits on the message's footer row now, beside the action bar,
      // rather than as a line of its own at the end of the body.
      className="text-muted-foreground/45 me-1 flex items-center gap-2 font-mono text-[10px] tabular-nums select-none"
    >
      {retried > 0 ? (
        <span
          className="text-amber-600/70 dark:text-amber-400/70"
          title={
            `A round failed and was sent again ${retried} time${retried === 1 ? "" : "s"}. ` +
            "The tools of the rounds before it had already run, so only the model request " +
            "repeated — nothing was done twice."
          }
        >
          sent again ×{retried}
        </span>
      ) : null}
      {rounds.length > 0 ? (
        <span
          title={`${rounds.length} request${rounds.length === 1 ? "" : "s"} · ${usageTitle(sumUsage(rounds))}\n${each}`}
        >
          {formatTokens(total)} tokens
        </span>
      ) : null}
    </div>
  );
}
