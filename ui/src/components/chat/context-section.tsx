import { useAuiState } from "@assistant-ui/react";

import { latestUsage } from "@/components/assistant-ui/thread";
import { formatTokens } from "@/lib/tokens";

/**
 * What is in his head right now, itemised.
 *
 * The compact meter under the composer said `54.5k (5%)` and kept everything else in a native
 * `title` — so the breakdown, which is the only part you can act on, was behind a hover on a
 * fourteen-pixel bar, in a tooltip the OS paints where it likes. A number you cannot decompose
 * tells you the window is filling and nothing about what to do, and "what is taking the room" is
 * the entire question: 300k of stale tool results and 300k of conversation are the same percentage
 * and completely different problems.
 *
 * So it lives here, itemised, in the panel that is already about what he is doing. Same reading
 * the meter used — `latestUsage` off the thread's own messages, live as of the round that just
 * landed — not a second fetch.
 *
 * Nothing at all when the window is unknown. `window === 0` is the honest answer for a local model
 * or one adopted before its size was recorded, and a percentage of an unknown total is worse than
 * no percentage.
 */

/** The threshold the loop folds at, from `agent_loop`. Named here so the section can say what is
 *  about to happen rather than only how full it is — 78% and 82% look alike and are not. */
const FOLDS_AT = 0.8;

//: Opacity steps on the foreground colour rather than a palette, so one set of values works in
//: both themes. Biggest category first, so the shades descend with the sizes.
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

export function ContextSection() {
  const messages = useAuiState((s) => s.thread.messages);
  const usage = latestUsage(messages);
  const context = usage?.context ?? usage?.baseline;
  if (!context?.window) return null;

  const percent = Math.round(context.share * 100);
  const folding = context.share >= FOLDS_AT;
  // Biggest first: the thing to do something about is the thing taking the most room.
  const ranked = [...context.lines].sort((a, b) => b.tokens - a.tokens);

  return (
    <div className="border-border/60 flex flex-col gap-2 border-b px-4 py-3">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[11px] font-medium">Context</span>
        <span
          className={`font-mono text-[11px] tabular-nums ${
            usage?.folded
              ? "text-red-600/80 dark:text-red-400/80"
              : folding
                ? "text-amber-600/80 dark:text-amber-400/80"
                : "text-muted-foreground"
          }`}
        >
          {percent}%
        </span>
      </div>

      <span aria-hidden className="bg-foreground/[0.07] flex h-1.5 overflow-hidden rounded-full">
        {ranked.map((line, index) => (
          <span
            key={line.key}
            className={SHADES[index] ?? SHADES[SHADES.length - 1]}
            style={{ width: `${line.share * 100}%` }}
          />
        ))}
      </span>

      {/* The counts, not only the percent. A whole point of a million-token window is that tens of
          thousands of tokens move the count and leave the percent unchanged for several turns. */}
      <p className="text-muted-foreground/70 font-mono text-[10px] tabular-nums">
        {formatTokens(context.used)} of {formatTokens(context.window)} ·{" "}
        {formatTokens(context.free)} free
      </p>

      <ul className="flex flex-col gap-1 pt-0.5">
        {ranked.map((line, index) => (
          <li key={line.key} className="flex items-center gap-2 text-[11px]">
            <span
              aria-hidden
              className={`size-2 shrink-0 rounded-[2px] ${SHADES[index] ?? SHADES[SHADES.length - 1]}`}
            />
            <span className="text-muted-foreground min-w-0 flex-1 truncate">{line.label}</span>
            <span className="text-muted-foreground/60 shrink-0 font-mono text-[10px] tabular-nums">
              {formatTokens(line.tokens)}
            </span>
            <span className="text-muted-foreground/40 w-8 shrink-0 text-end font-mono text-[10px] tabular-nums">
              {Math.round(line.share * 100)}%
            </span>
          </li>
        ))}
      </ul>

      {/* What happens next, rather than leaving you to know that 80% is the number that matters. */}
      <p className="text-muted-foreground/50 text-[10px]">
        {usage?.folded
          ? "Earlier steps of this turn were folded into notes to make room."
          : folding
            ? "Near the fold — earlier steps will be summarised to make room."
            : `Folds at ${Math.round(FOLDS_AT * 100)}%.`}
      </p>
    </div>
  );
}
