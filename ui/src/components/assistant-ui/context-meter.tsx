import type { ContextLedger } from "@/lib/backend/types";
// The one definition. This file kept a private copy of the same 0.8, so the meter could have gone
// on colouring at a threshold the server had stopped folding at.
import { FOLDS_AT } from "@/lib/context-groups";
import { formatCompact, formatTokens } from "@/lib/tokens";

/**
 * How full the model's context is, and what is filling it.
 *
 * Shown whenever the window is known, not held back until half full. That threshold was
 * calibrated for a window small enough that an ordinary turn could plausibly reach it — on a
 * 1M+-token model it never fires at all, since real usage sits at a few percent, so "hidden
 * until it matters" quietly became "hidden." The muted grey tone at low usage is the answer to
 * the furniture worry, not hiding it: quiet until there is something to say, not absent.
 *
 * Nothing at all when the window is unknown: `window === 0` is the honest answer for a local
 * model or one adopted before its size was recorded, and a percentage of an unknown total is
 * the one thing worse than no meter. The token count is still real, so that is what the title
 * carries in that case.
 *
 * Colour is earned, not decorative. Grey while there is room, amber approaching the fold, red
 * once he is folding — three states, matching what is actually about to happen to his memory.
 */



//: Enough distinct steps that adjacent categories separate, without inventing a palette. These
//: are opacity steps on the foreground colour, so the bar works in either theme without a
//: second set of values.
const SHADES = [
  "bg-foreground/70",
  "bg-foreground/55",
  "bg-foreground/45",
  "bg-foreground/35",
  "bg-foreground/28",
  "bg-foreground/22",
  "bg-foreground/16",
  "bg-foreground/12",
  "bg-foreground/[0.09]",
  "bg-foreground/[0.07]",
];

export function ContextMeter({ context, folded }: { context: ContextLedger; folded?: boolean }) {
  if (!context.window) return null;

  const percent = Math.round(context.share * 100);
  const tone = folded
    ? "text-red-600/70 dark:text-red-400/70"
    : context.share >= FOLDS_AT
      ? "text-amber-600/80 dark:text-amber-400/80"
      : "text-muted-foreground/45";

  // Biggest first: the thing to do something about is the thing taking the most room.
  const ranked = [...context.lines].sort((a, b) => b.tokens - a.tokens);
  const breakdown = ranked
    .map((line) => `${line.label}: ${formatTokens(line.tokens)} (${Math.round(line.share * 100)}%)`)
    .join("\n");

  return (
    <div
      data-slot="kith_context-meter"
      className={`me-2 flex items-center gap-1.5 font-mono text-[10px] tabular-nums select-none ${tone}`}
      title={
        `Context ${formatTokens(context.used)} of ${formatTokens(context.window)} (${percent}%)\n` +
        `${formatTokens(context.free)} free\n\n${breakdown}` +
        (folded ? "\n\nEarlier steps of this turn were folded into notes to make room." : "")
      }
    >
      <span
        aria-hidden
        className="bg-foreground/[0.07] flex h-1 w-14 overflow-hidden rounded-full"
      >
        {ranked.map((line, index) => (
          <span
            key={line.key}
            className={SHADES[index] ?? SHADES[SHADES.length - 1]}
            style={{ width: `${line.share * 100}%` }}
          />
        ))}
      </span>
      {/* The count, not just the percent: a whole point of a million-token window is tens
          of thousands of tokens, so the percent alone can sit unchanged for several turns
          in a row while the conversation is genuinely growing — see `formatCompact`. */}
      <span>
        {formatCompact(context.used)} ({percent}%)
      </span>
      {folded ? <span title="folded to make room">folded</span> : null}
    </div>
  );
}
