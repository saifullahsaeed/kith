import { useEffect, useState } from "react";

import { useLayout } from "@/components/shell/layout/store";
import { foldNow } from "@/lib/commands";
import { useFocusedChat } from "@/lib/focused-chat";
import { byGroup, FOLDS_AT } from "@/lib/context-groups";
import { cn } from "@/lib/utils";
import type { ContextLedger } from "@/lib/backend/types";

/**
 * What is in his head right now, itemised, with the button that does something about it.
 *
 * The compact meter under the composer said `54.5k (5%)` and kept everything else in a native
 * `title` — so the breakdown, which is the only part you can act on, sat behind a hover on a
 * fourteen-pixel bar, in a tooltip the OS paints where it likes. A number you cannot decompose says
 * the window is filling and nothing about what to do, and that is the whole question: 300k of stale
 * tool results and 300k of conversation are the same percentage and completely different problems.
 *
 * A card now, with the five groups always named under the bar. The itemised list of every category
 * that used to fold open here is the Context tab's job, one click away on the card's title — two
 * copies of the same breakdown a tab apart was the Work panel's old mistake in miniature.
 *
 * Nothing renders at all when the window is unknown. A local model reports `window === 0`, and a
 * percentage of an unknown total is worse than no percentage.
 */

/** `412k`, `1M`, `1.05M` — the figures the card is read at, where a comma-grouped count would
 *  make every row a different width. The exact numbers are one click away, on the Context tab. */
function short(count: number): string {
  if (count < 1_000) return String(count);
  if (count < 1_000_000) return `${Math.round(count / 1_000)}k`;
  return `${Number((count / 1_000_000).toFixed(2))}M`;
}

export function ContextSection({ conversationId }: { conversationId?: string }) {
  const openSurface = useLayout((state) => state.open);
  /* Read from the focused chat rather than from a runtime.
   *
   * This panel is its own pane now, outside every chat's provider, so `useAuiState` throws
   * here — during render, which took the whole Work panel down with it. The focused chat
   * publishes what this needs; see `lib/focused-chat`. */
  const usage = useFocusedChat((state) => state.usage);
  const running = useFocusedChat((state) => state.running);
  const [folding, setFolding] = useState(false);
  // The reading a fold leaves behind. A fold takes no reading of its own, so until the next turn
  // reports in, the thread's latest usage still describes the conversation as it was *before* the
  // fold — which is what made `/fold` look like it had done nothing. Dropped the moment a turn
  // starts, because that turn measures the window itself and its figure is the true one.
  const [afterFold, setAfterFold] = useState<ContextLedger>();
  const [note, setNote] = useState("");
  useEffect(() => {
    if (running) {
      setAfterFold(undefined);
      setNote("");
    }
  }, [running]);

  const context = afterFold ?? usage?.context ?? usage?.baseline;
  if (!context?.window) return null;

  const percent = Math.round(context.share * 100);
  const nearFold = context.share >= FOLDS_AT;
  const tone = afterFold
    ? "text-muted-foreground"
    : usage?.folded
      ? "text-red-600/80 dark:text-red-400/80"
      : nearFold
        ? "text-amber-600/80 dark:text-amber-400/80"
        : "text-muted-foreground";

  // Fixed order, so a segment stays where it was and the bar does not reshuffle between turns.
  // Shared with the detail screen this opens — see `lib/context-groups` for why the grouping and
  // the palette live in one place rather than two.
  const groups = byGroup(context.lines, context.used);

  const fold = async () => {
    if (!conversationId || folding) return;
    setFolding(true);
    setNote("Folding…");
    const result = await foldNow(conversationId);
    setNote(result.note);
    if (result.reading) setAfterFold(result.reading);
    setFolding(false);
  };

  /* The whole breakdown — every category, the prompt itself, and which reads are repeats — is the
   * Context tab beside this one. `open` rather than a route: for a chat that is on screen it
   * attaches to the same column, which is the tab row switching to it. */
  const openDetail = () => {
    if (conversationId) openSurface({ surface: "context", conversationId });
  };

  // What happens next, when it is more than the standing threshold the legend already states.
  const said =
    note ||
    (afterFold
      ? "Folded. This is the window it left."
      : usage?.folded
        ? "Earlier steps of this turn were folded into notes to make room."
        : nearFold
          ? "Near the fold — earlier steps will be summarised to make room."
          : "");

  return (
    <section
      aria-label="Context"
      className="flex shrink-0 flex-col gap-2.5 rounded-[14px] border border-border bg-card px-3.5 py-3"
    >
      <div className="flex min-w-0 items-center gap-2">
        <button
          type="button"
          onClick={openDetail}
          disabled={!conversationId}
          className="cursor-pointer text-[13px] font-semibold hover:underline disabled:cursor-default disabled:no-underline"
          title="Open the full breakdown — every call, and which of them are repeats"
        >
          Context
        </button>
        {/* The counts, not only the percent. A whole point of a million-token window is that tens
            of thousands of tokens move the count and leave the percent unchanged. */}
        <span className={cn("min-w-0 truncate text-[12px] tabular-nums", tone)}>
          {short(context.used)} of {short(context.window)} · {percent}%
        </span>
        <span className="flex-1" />
        {/* The one thing you can do about a full window, next to the number that says it is
            full. Disabled mid-turn — a fold rewrites the history the turn is reading. */}
        <button
          type="button"
          onClick={fold}
          disabled={!conversationId || folding || running}
          title={
            running
              ? "Wait for the turn to finish — a fold rewrites the history it is reading."
              : "Summarise the older turns now, instead of waiting for the window to fill"
          }
          className="h-[26px] shrink-0 cursor-pointer rounded-[7px] border border-foreground/14 px-[9px] text-[12px] text-foreground/85 transition-colors hover:bg-accent disabled:cursor-default disabled:opacity-50"
        >
          Fold now
        </button>
      </div>

      {/* Drawn against the window, with the fold marked on it, so the bar says how far there is to
          go as well as what it is made of. A 2px gap between segments, because adjacent fills of
          similar lightness read as one block without one — and it is the secondary encoding the
          palette's light-mode contrast warn asks for. */}
      <div aria-hidden className="relative h-2">
        <span className="bg-muted flex h-full gap-0.5 overflow-hidden rounded-full">
          {groups.map((group) => (
            <span
              key={group.key}
              className={`${group.swatch} shrink-0`}
              style={{ width: `${(group.tokens / context.window) * 100}%` }}
            />
          ))}
        </span>
        <span
          className="bg-foreground/50 absolute -top-0.5 -bottom-0.5 w-0.5"
          style={{ left: `${FOLDS_AT * 100}%` }}
        />
      </div>

      {/* The legend is what makes the colours legible — the palette clears every contrast gate on
          the dark surface and warns on the light one, and a warn obliges labels, not colour alone. */}
      <ul className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] tabular-nums text-muted-foreground">
        {groups.map((group) => (
          <li key={group.key} className="flex min-w-0 items-center gap-1.5">
            <span aria-hidden className={`size-2 shrink-0 rounded-[2px] ${group.swatch}`} />
            <span className="min-w-0 truncate">{group.label}</span>
            <span className="flex-1" />
            <span className="shrink-0">{short(group.tokens)}</span>
          </li>
        ))}
        <li className="flex min-w-0 items-center gap-1.5 text-muted-foreground/80">
          <span aria-hidden className="bg-foreground/50 h-0.5 w-2 shrink-0" />
          Folds at {Math.round(FOLDS_AT * 100)}%
        </li>
      </ul>

      {said ? <p className="text-[11px] text-muted-foreground">{said}</p> : null}
    </section>
  );
}
