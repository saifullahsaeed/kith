import { cn } from "@/lib/utils";
import type { Tally } from "./format";

/**
 * One bar, three colours: done, being worked, approved and waiting to start. Plans awaiting a
 * decision are the empty track — they are real work nobody has said yes to yet.
 */
export function ProgressBar({ counts, className }: { counts: Tally; className?: string }) {
  const pct = (n: number) => (counts.total ? `${(n / counts.total) * 100}%` : "0%");
  return (
    <div
      className={cn("bg-muted flex h-1.5 w-full gap-0.5 overflow-hidden rounded-full", className)}
      role="img"
      aria-label={`${counts.done} of ${counts.total} done`}
    >
      {counts.done ? <span className="bg-roam rounded-full" style={{ width: pct(counts.done) }} /> : null}
      {counts.working ? <span className="bg-kith rounded-full" style={{ width: pct(counts.working) }} /> : null}
      {counts.approved ? (
        <span className="bg-kith/45 rounded-full" style={{ width: pct(counts.approved) }} />
      ) : null}
    </div>
  );
}

/** The same counts in words, in the order you would ask about them. */
export function Breakdown({ counts }: { counts: Tally }) {
  if (!counts.total) return <span className="text-muted-foreground/70">no tasks yet</span>;
  return (
    <span className="flex flex-wrap items-center gap-x-3 gap-y-1 tabular-nums">
      <span className="text-foreground font-medium">
        {counts.done} of {counts.total} done
      </span>
      {counts.working ? <span className="text-kith">{counts.working} working</span> : null}
      {counts.planning ? <span>{counts.planning} to approve</span> : null}
      {counts.approved ? <span>{counts.approved} up next</span> : null}
    </span>
  );
}

/**
 * The 2026-09-30 redesign's bar: 4px, and coloured by state rather than by who is acting — done
 * green, being worked the same green at half strength, approved and not yet started blue. Plans
 * waiting for your OK are still the empty track. `ProgressBar` above is the older one, left as it
 * is for the screens that have not moved to the new design.
 *
 * No tasks, no segments: the caller decides whether an empty track is worth drawing at all.
 */
export function SegmentBar({ counts, className }: { counts: Tally; className?: string }) {
  const segment = (n: number, tone: string) =>
    n && counts.total ? (
      <span className={cn("h-1 shrink-0 rounded-full", tone)} style={{ width: `${(n / counts.total) * 100}%` }} />
    ) : null;
  return (
    <span
      role="img"
      aria-label={`${counts.done} of ${counts.total} done`}
      className={cn("bg-muted flex h-1 shrink-0 gap-0.5 overflow-hidden rounded-full", className)}
    >
      {segment(counts.done, "bg-roam")}
      {segment(counts.working, "bg-roam/45")}
      {segment(counts.approved, "bg-sky-500/70 dark:bg-[#6FA8F0]/70")}
    </span>
  );
}
