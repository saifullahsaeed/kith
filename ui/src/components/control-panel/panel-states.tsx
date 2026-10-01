/** What the panel shows before it has his mind, when it can't get it, and when what it has is old.
 *
 * From the "States" board. Three rules it commits to, and the reason each replaced what was here:
 *
 *   - **Loading has no spinner.** The page's own frame — its title, the line under it, the shape of
 *     its list — is there at once, and only his data waits. A centred spinner said "loading his
 *     mind" over an empty window, which looks the same as a window that will never fill.
 *   - **Failure says so.** `/api/brain` failing used to leave that spinner turning forever: the
 *     query had settled into an error and nothing on screen read it. Now it is a sentence of what
 *     happened, the way to try again, and the raw error under it for whoever has to fix it.
 *   - **Stale stays readable.** When a refetch fails after a good answer, the good answer stays on
 *     screen — nothing greys out — and one amber line says how old it is.
 */
import { useState, type CSSProperties } from "react";
import { Unplug } from "lucide-react";
import { cn } from "@/lib/utils";
import { BUTTON, PageTitle } from "./chrome";
import type { Tab } from "./types";

/** Each page's title and the line under it, as its board has them — the frame that shows while
 *  its data loads, or can't. */
export function frameFor(tab: Tab): { title: string; subtitle: string } {
  switch (tab) {
    case "overview": {
      const date = new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
      return { title: "Today", subtitle: `${date} — what's waiting on you, what he's on, and what changed since this morning.` };
    }
    case "projects":
      return { title: "Projects", subtitle: "Everything he's working on. Open one for its board, conversations and outputs." };
    case "reminders":
      return { title: "Reminders", subtitle: "Nudges that fire once, at a set time." };
    case "schedules":
      return { title: "Schedules", subtitle: "Standing jobs he runs on a cadence." };
    case "memory":
      return { title: "Memory", subtitle: "What he holds onto — and how close to hand." };
    case "journal":
      return { title: "Journal", subtitle: "His private diary — how the days felt." };
    case "lifetime":
      return { title: "Lifetime", subtitle: "Everything he's done, newest first." };
    case "sources":
      return { title: "Sources", subtitle: "He reads it, remembers it, and recalls it on demand." };
    case "workspace":
      return { title: "Files", subtitle: "His folder on this machine. Everything he makes lands here." };
  }
}

/* The board's pulse: opacity only, walking down the list by a per-row delay. Not `animate-pulse`,
 * whose curve and floor are different enough to read as a different thing beside the board, and
 * still under reduced motion — the rows standing still is the whole of the signal then. */
const SHIMMER = `
@keyframes kith-shimmer { 0%, 100% { opacity: .4 } 50% { opacity: 1 } }
.kith-shimmer { animation: kith-shimmer 1.4s ease-in-out infinite; }
@media (prefers-reduced-motion: reduce) { .kith-shimmer { animation: none; opacity: .7; } }
`;

function Bar({ className, delay, style }: { className: string; delay?: number; style?: CSSProperties }) {
  return (
    <span
      aria-hidden
      className={cn("block shrink-0 rounded-full", delay !== undefined && "kith-shimmer", className)}
      style={{ ...style, ...(delay !== undefined ? { animationDelay: `${delay}s` } : null) }}
    />
  );
}

/** Line widths for the skeleton rows, from the board — irregular so it reads as text, not as a grid. */
const ROWS: [string, string][] = [
  ["64%", "40%"],
  ["52%", "58%"],
  ["71%", "34%"],
  ["46%", "50%"],
  ["60%", "42%"],
  ["55%", "30%"],
];

/** The list's skeleton: a header bar, then rows shaped like a dated list — date, tile, two lines,
 *  something on the right. Enough rows to fill the window; the card clips the rest. */
function ListSkeleton() {
  return (
    <div className="border-border bg-card flex min-h-0 flex-1 flex-col gap-0.5 overflow-hidden rounded-xl border px-2 py-1.5">
      <div className="flex h-7 shrink-0 items-center px-2">
        <Bar className="bg-muted h-2 w-16" delay={0} />
      </div>
      {Array.from({ length: 24 }, (_, i) => {
        const [one, two] = ROWS[i % ROWS.length];
        return (
          <div
            key={i}
            className="kith-shimmer flex h-[46px] shrink-0 items-center gap-3 rounded-[10px] px-2"
            style={{ animationDelay: `${((i % 12) * 0.12).toFixed(2)}s` }}
          >
            <Bar className="bg-muted h-2 w-11" />
            <span className="bg-muted/60 size-[26px] shrink-0 rounded-lg" />
            <span className="flex min-w-0 flex-1 flex-col gap-2">
              <Bar className="bg-muted h-[9px]" style={{ width: one }} />
              <Bar className="bg-muted/60 h-[7px]" style={{ width: two }} />
            </span>
            <Bar className="bg-muted/60 h-2 w-[72px]" />
          </div>
        );
      })}
    </div>
  );
}

/** Today's skeleton is Today's layout — the five cards with their real titles and only the rows
 *  missing — as the board draws it behind the search palette. */
function TodaySkeleton() {
  const card = (title: string, widths: string[], gap: string, i: number) => (
    <div key={title} className="border-border bg-card flex min-h-0 flex-col overflow-hidden rounded-[14px] border">
      <div className="text-foreground/80 flex h-[46px] shrink-0 items-center border-b border-border/80 px-4 text-[13px] font-semibold">
        {title}
      </div>
      <div className={cn("flex flex-col p-4", gap)}>
        {widths.map((w, j) => (
          <Bar key={j} className="bg-muted/60 h-2.5" style={{ width: w }} delay={((i * 3 + j) % 12) * 0.12} />
        ))}
      </div>
    </div>
  );
  return (
    <>
      <div className="grid h-[402px] shrink-0 grid-cols-[1.3fr_1.1fr_0.8fr] gap-4">
        {card("Needs you", ["78%", "52%", "88%", "64%", "82%", "46%", "70%", "58%"], "gap-3.5", 0)}
        {card("Working now", ["84%", "72%", "90%", "66%", "80%", "40%", "62%"], "gap-3.5", 1)}
        {card("Coming up", ["60%", "86%", "44%", "74%"], "gap-3.5", 2)}
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-[2.2fr_1fr] gap-4">
        {card("What changed today", ["70%", "54%", "82%", "48%", "76%", "60%", "66%", "38%", "72%"], "gap-4", 3)}
        {card("His mind today", ["40%", "86%", "72%", "90%", "58%", "80%"], "gap-4", 4)}
      </div>
    </>
  );
}

/** The panel before its first answer: the page's frame, and the shape of what will fill it. */
export function PanelLoading({ tab, task }: { tab: Tab; task?: boolean }) {
  const frame = frameFor(tab);
  return (
    <div aria-busy="true" aria-label="Loading" className="flex h-full flex-col gap-[22px] px-10 pt-7 pb-7">
      <style>{SHIMMER}</style>
      {/* A task page has its own header, and it isn't the tab's — so a task loads as rows alone. */}
      {task ? null : (
        <PageTitle
          title={frame.title}
          count={tab === "overview" ? undefined : <Bar className="bg-muted/60 inline-block h-2.5 w-7 align-middle" delay={0} />}
          subtitle={frame.subtitle}
        />
      )}
      {tab === "overview" && !task ? <TodaySkeleton /> : <ListSkeleton />}
    </div>
  );
}

/** "2 minutes ago", "just now". */
export function ago(since: number, now: number): string {
  const seconds = Math.max(0, Math.round((now - since) / 1000));
  if (seconds < 45) return "just now";
  if (seconds < 90) return "a minute ago";
  const minutes = Math.round(seconds / 60);
  if (minutes < 90) return `${minutes} minutes ago`;
  const hours = Math.round(minutes / 60);
  return `${hours} hour${hours === 1 ? "" : "s"} ago`;
}

/**
 * What a failed `/api/brain` means, in one sentence.
 *
 * `fetchBrain` throws `"/api/brain 502"` for an answer that isn't OK and the browser's own
 * TypeError when nothing answered. A gateway status is the dev proxy saying the server behind it
 * is gone — the same fact as a refused connection, so it reads the same; any other status is the
 * server answering with an error of its own, which is a different thing to go and fix.
 */
export function whatHappened(error: unknown, since: number, now: number): string {
  const message = error instanceof Error ? error.message : String(error ?? "");
  const status = Number(message.match(/\b([45]\d\d)\b/)?.[1] ?? 0);
  const when = ago(since, now);
  if (!status || status === 502 || status === 503 || status === 504) return `The Kith server stopped responding ${when}.`;
  return `The Kith server answered with an error (${status}) ${when}.`;
}

/** How often the panel asks again by itself while his mind can't be reached. */
export const RETRY_EVERY = 15_000;

/** His mind could not be read at all: what happened, the way to try again, and the raw error. */
export function PanelError({
  tab,
  task,
  error,
  since,
  now,
  retrying,
  onRetry,
}: {
  tab: Tab;
  task?: boolean;
  error: unknown;
  /** When the first failure arrived — it stays the same across retries that also fail. */
  since: number;
  now: number;
  retrying: boolean;
  onRetry: () => void;
}) {
  const frame = frameFor(tab);
  // The first failure, held: `since` moves on with every retry that also fails, and "stopped
  // responding just now", forever, would be the endless spinner again in words.
  const [first] = useState(since);
  const raw = error instanceof Error ? error.message : String(error ?? "Unknown error");
  return (
    <div className="flex h-full flex-col px-10 pt-7 pb-7">
      {task ? null : <PageTitle title={frame.title} subtitle={frame.subtitle} />}
      <div role="alert" className="flex min-h-0 flex-1 flex-col items-center justify-center px-2 text-center">
        <span
          aria-hidden
          className="border-border bg-muted/60 text-muted-foreground flex size-11 items-center justify-center rounded-xl border"
        >
          <Unplug className="size-5" />
        </span>
        <span className="mt-4 text-[15px] font-semibold">He isn&apos;t answering</span>
        <span className="text-muted-foreground mt-1.5 max-w-[290px] text-[12.5px] leading-normal">
          {whatHappened(error, first || since, now)}
        </span>
        <div className="mt-[18px] flex gap-2">
          {/* No "Open logs": the app has no logs screen to send you to, and a button that opens
              nothing is worse than its absence. */}
          <button type="button" onClick={onRetry} disabled={retrying} className={BUTTON.primary}>
            {retrying ? "Trying…" : "Try again"}
          </button>
        </div>
        <span className="text-muted-foreground/80 mt-3.5 text-[11.5px]">
          It also retries by itself every {RETRY_EVERY / 1000} seconds.
        </span>
      </div>
      <div className="border-border/80 bg-sidebar flex shrink-0 flex-col gap-[3px] rounded-[10px] border px-3 py-[9px]">
        <span className="text-muted-foreground/80 text-[10.5px] font-semibold tracking-[0.1em] uppercase">Last error</span>
        <span className="text-foreground/80 truncate font-mono text-[11.5px]" title={raw}>
          {raw}
        </span>
      </div>
    </div>
  );
}

/** What is on screen is older than it should be and the refetch is failing — said once, in amber,
 *  above the page, with the page left exactly as it was. */
export function StaleBanner({ since, now }: { since: number; now: number }) {
  const minutes = Math.max(1, Math.round((now - since) / 60_000));
  return (
    <div
      role="status"
      className="border-kith/25 bg-kith/[0.08] mx-10 mt-5 flex h-[30px] shrink-0 items-center gap-2 rounded-lg border px-2.5"
    >
      <span className="kith-breathe bg-kith size-1.5 shrink-0 rounded-full" style={{ animationDuration: "1.6s" }} />
      <span className="text-kith truncate text-[11.5px]">
        Showing what he knew {minutes} minute{minutes === 1 ? "" : "s"} ago — reconnecting…
      </span>
    </div>
  );
}

/** How old an answer has to be, with the refetch failing, before the banner says so. */
export const STALE_AFTER = 120_000;
