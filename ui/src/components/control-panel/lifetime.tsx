/**
 * Lifetime: everything he has done, newest first.
 *
 * It was a spine with one dot per timeline row, and most of the rows said the same thing — a
 * 54-screen job was 54 lines of "Finished something on …", so a day's real story (he filed a
 * project, built it, wrote down what he learnt) was a scroll away under the repeats. Three things
 * replace that:
 *
 *   - a strip of the last thirty days, so "when was he busy" is answered before you read a row,
 *     and any day is one click away (as is any date at all, from "Jump to date");
 *   - kind chips, with how many of each there are;
 *   - each day's events folded — every output on one task is one row ("Finished 54 things on …")
 *     with the first few named and the rest a click away. The folding is `lifetime-fold.ts`.
 *
 * Header and strip stay put while the timeline scrolls under its own sticky day headings, so the
 * strip is still there to jump with from three weeks down.
 *
 * All of it, back to the first day. The timeline arrives a page at a time (`timeline-pages.ts`):
 * the next older page is asked for as you near the end of the list, the strip's thirty days and any
 * date you jump to are paged in before they are drawn, and the chips count all of history from the
 * server's totals rather than whatever happens to have loaded. It used to stop at the newest 500
 * things and say so in a footnote.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useNavigate } from "react-router";
import {
  Bell,
  Brain,
  CalendarDays,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  CirclePause,
  FileText,
  Layers,
  ListChecks,
  MessageCircleQuestion,
  NotebookPen,
} from "lucide-react";

import type { BrainSnapshot, TimelineEvent } from "@/lib/backend/brain";
import { cn } from "@/lib/utils";
import { BUTTON, Chip, Empty, KindTile, PageTitle, Segmented } from "./chrome";
import { clock, dayKey, keyMidnight, longDate, monthIndex, plural, relativeDay } from "./day-format";
import { useFitPanel } from "./fit-panel";
import { HeatCalendar } from "./heat-calendar";
import { plain, sentence } from "./journal-links";
import { activityStrip, breakdown, buildDays, FILTER_KIND, spanLabel, toLifeEvents } from "./lifetime-fold";
import type { Burst, LifeEvent, LifeFilter, LifeKind, StripDay } from "./lifetime-fold";
import type { Paging } from "./timeline-pages";
import type { Tab } from "./types";

/** How near the end of the list, in screens, the next older page is asked for — far enough that
 *  it has usually landed before you get there. */
const NEAR_END = 1.5;
/** The most pages fetched to reach one date (the strip's first day, or a day picked from "Jump to
 *  date") — 10,000 events at the server's page of 500 — before going as far as they got. */
const REACH_PAGES = 20;
/** The most pages pulled in without you scrolling, to fill a screen that a filter or a search has
 *  left short. Past it, "Load older" at the foot of the list asks. */
const FILL_PAGES = 6;
/** How many of a job's outputs are named on its folded row before "Show all". */
const CHIPS = 5;

const FILTERS: [LifeFilter, string][] = [
  ["all", "All"],
  ["tasks", "Tasks"],
  ["outputs", "Outputs"],
  ["memory", "Memory"],
  ["journal", "Journal"],
  ["reminders", "Reminders"],
];

const ICON: Record<LifeKind, ReactNode> = {
  task: <ListChecks />,
  output: <FileText />,
  memory: <Brain />,
  journal: <NotebookPen />,
  reminder: <Bell />,
  asked: <MessageCircleQuestion />,
  stopped: <CirclePause />,
};

export function Lifetime({
  snap,
  events,
  searching = false,
  paging,
  onOpenTask,
  onNavigate,
}: {
  snap: BrainSnapshot;
  /** The timeline loaded so far, already narrowed by the panel's search. */
  events: TimelineEvent[];
  /** Whether that search is narrowing it — the chips then count what matches, not all history. */
  searching?: boolean;
  /** Where the loaded pages reach and how to load more. */
  paging: Paging;
  onOpenTask: (id: number) => void;
  onNavigate: (tab: Tab) => void;
}) {
  const navigate = useNavigate();
  const [page, height] = useFitPanel<HTMLDivElement>();
  const [filter, setFilter] = useState<LifeFilter>("all");
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set());
  const scroller = useRef<HTMLDivElement>(null);
  const sections = useRef(new Map<string, HTMLElement>());

  const all = useMemo(() => toLifeEvents(events, snap), [events, snap]);
  /* All of history while nothing narrows it — the server counts that. A search narrows to what
     matches, which only the loaded pages can say, so then it counts those. */
  const counts = useMemo(() => {
    if (paging.totals && !searching) return paging.totals;
    const out: Record<LifeFilter, number> = { all: all.length, tasks: 0, outputs: 0, memory: 0, journal: 0, reminders: 0 };
    for (const event of all) {
      for (const [chip, kind] of Object.entries(FILTER_KIND) as [Exclude<LifeFilter, "all">, LifeKind][]) {
        if (event.kind === kind) out[chip] += 1;
      }
    }
    return out;
  }, [all, paging.totals, searching]);
  const shown = useMemo(
    () => (filter === "all" ? all : all.filter((event) => event.kind === FILTER_KIND[filter])),
    [all, filter],
  );
  const days = useMemo(() => buildDays(shown), [shown]);
  const strip = useMemo(() => activityStrip(shown), [shown]);
  const perDay = useMemo(() => new Map(days.map((day) => [day.key, day.count])), [days]);

  // A new filter is a new list; staying 4,000px down it would land you somewhere arbitrary.
  useEffect(() => {
    scroller.current?.scrollTo({ top: 0 });
  }, [filter]);

  /** Scroll to a day, or — for a day with nothing on it — the nearest earlier day that has
   *  something, which is where that date would sit in the list. Only for a day already loaded;
   *  `goTo` pages back to one first. */
  const jump = (key: string) => {
    const target = days.find((day) => day.key <= key) ?? days[days.length - 1];
    const section = target ? sections.current.get(target.key) : undefined;
    const el = scroller.current;
    if (!section || !el) return;
    // Glide a screen or two; cut straight to anything further, which a glide would take seconds
    // to reach and where the motion tells you nothing.
    const near = Math.abs(section.offsetTop - el.scrollTop) < el.clientHeight * 2.5;
    el.scrollTo({ top: section.offsetTop, behavior: near ? "smooth" : "auto" });
  };

  /* ── Paging ──
     Three things ask for older pages, and all go through `paging.loadMore`, which does nothing
     while a page is on its way: nearing the end of the list (`onScroll`, and after each page
     lands, for a list too short to scroll); the strip, whose thirty days must all be loaded to
     be drawn true; and a jump to a day not loaded yet. The last two are one rule — load until
     something older than a date has arrived — with a cap, so a date before his first day stops
     at the first day rather than asking forever. */
  const covered = (ms: number) => !paging.hasMore || (paging.reached !== null && paging.reached < ms);
  const [target, setTarget] = useState<string | null>(null);
  const want = Math.min(strip[0]?.midnight ?? 0, target ? keyMidnight(target) : Number.POSITIVE_INFINITY);
  const reaching = useRef({ want: 0, pages: 0 });
  const filling = useRef(0);

  useEffect(() => {
    if (reaching.current.want !== want) reaching.current = { want, pages: 0 };
    if (paging.loadingMore || paging.failed || covered(want)) return;
    if (reaching.current.pages >= REACH_PAGES) return;
    reaching.current.pages += 1;
    paging.loadMore();
  });

  // A jump waiting on pages goes once its day has arrived, or once there is no getting there.
  useEffect(() => {
    if (!target || paging.loadingMore) return;
    const there = covered(keyMidnight(target));
    if (!there && !paging.failed && reaching.current.pages < REACH_PAGES) return;
    setTarget(null);
    jump(target);
  });

  const goTo = (key: string) => {
    if (covered(keyMidnight(key))) jump(key);
    else setTarget(key);
  };

  const nearEnd = () => {
    const el = scroller.current;
    return !!el && el.clientHeight > 0 && el.scrollHeight - el.scrollTop - el.clientHeight < el.clientHeight * NEAR_END;
  };
  const onScroll = () => {
    filling.current = 0;
    if (nearEnd()) paging.loadMore();
  };
  // A list that does not reach the bottom of the screen has nothing to scroll, so nothing would
  // ever ask for the page that fills it.
  useEffect(() => {
    if (!paging.hasMore || paging.loadingMore || paging.failed || filling.current >= FILL_PAGES || !nearEnd()) return;
    filling.current += 1;
    paging.loadMore();
  }, [days, paging]);

  const opener = (event: LifeEvent): (() => void) | undefined => {
    switch (event.kind) {
      case "task":
      case "output":
        return event.taskId != null ? () => onOpenTask(event.taskId!) : () => onNavigate("projects");
      case "memory":
        return () => onNavigate("memory");
      case "journal":
        return () => onNavigate("journal");
      case "reminder":
        return () => onNavigate("reminders");
      case "asked":
      case "stopped":
        return event.link ? () => navigate(event.link!) : undefined;
    }
  };

  const toggle = (key: string) =>
    setExpanded((was) => {
      const next = new Set(was);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  return (
    <div ref={page} style={{ height }} className="-mb-7 flex h-[calc(100dvh-72px)] flex-col gap-[22px]">
      <PageTitle title="Lifetime" subtitle="Everything he's done, newest first.">
        <Segmented
          label="Show kind"
          value={filter}
          onChange={setFilter}
          options={FILTERS.map(([value, label]) => ({
            value,
            label: (
              <>
                {label}
                <span className="text-muted-foreground/80 ms-1.5 tabular-nums">{counts[value].toLocaleString()}</span>
              </>
            ),
          }))}
        />
        <JumpToDate
          className="ms-2"
          counts={perDay}
          oldest={paging.oldest ?? days[days.length - 1]?.midnight ?? Date.now()}
          going={target}
          unknown={
            paging.hasMore && paging.reached !== null
              ? (key) => key < dayKey(paging.reached!) && (paging.oldest === null || key >= dayKey(paging.oldest))
              : undefined
          }
          onPick={goTo}
        />
      </PageTitle>

      <ActivityStrip strip={strip} onPick={goTo} />

      <div ref={scroller} onScroll={onScroll} className="relative min-h-0 flex-1 overflow-y-auto pb-7">
        {days.length === 0 ? (
          <Empty title={all.length ? "Nothing of this kind" : "Nothing here yet"}>
            {events.length === 0 && all.length === 0
              ? "When he does something — files a task, remembers, writes — it lands here."
              : "Nothing he's done matches this filter and your search."}
          </Empty>
        ) : (
          days.map((day, index) => (
            <section
              key={day.key}
              ref={(node) => {
                if (node) sections.current.set(day.key, node);
                else sections.current.delete(day.key);
              }}
              aria-label={longDate(day.midnight)}
              className={cn("flex flex-col", index > 0 && "mt-3.5")}
            >
              <header className="bg-background border-border/80 sticky top-0 z-20 mb-1 flex h-[34px] shrink-0 items-center gap-2.5 border-b px-2">
                <h2 className="text-foreground text-[13px] font-semibold">{relativeDay(day.midnight)}</h2>
                <span className="text-muted-foreground text-[12.5px]">{longDate(day.midnight)}</span>
                <span className="flex-1" />
                <span className="text-muted-foreground/80 text-xs tabular-nums">{plural(day.count, "thing")}</span>
              </header>
              <div className="flex flex-col gap-0.5">
                {day.bursts.map((burst) => (
                  <Row
                    key={burst.key}
                    burst={burst}
                    open={expanded.has(burst.key)}
                    onToggle={() => toggle(burst.key)}
                    opener={opener}
                  />
                ))}
              </div>
            </section>
          ))
        )}
        {days.length > 0 || paging.hasMore ? (
          <ListEnd paging={paging} first={paging.oldest ?? days[days.length - 1]?.midnight ?? null} />
        ) : null}
      </div>
    </div>
  );
}

/** The foot of the list: older things on their way, a way to ask for them, or the beginning. */
function ListEnd({ paging, first }: { paging: Paging; first: number | null }) {
  return (
    <div role="status" className="text-muted-foreground/80 flex min-h-[52px] items-center gap-3 px-2 pt-5 text-xs">
      {paging.loadingMore ? (
        "Loading older things…"
      ) : paging.failed ? (
        <>
          Couldn&rsquo;t load older things.
          <button type="button" onClick={paging.loadMore} className={cn(BUTTON.secondary, "h-7 px-2.5 text-xs")}>
            Try again
          </button>
        </>
      ) : paging.hasMore ? (
        <button type="button" onClick={paging.loadMore} className={cn(BUTTON.secondary, "h-7 px-2.5 text-xs")}>
          Load older
        </button>
      ) : !paging.totals ? (
        // A server from before paging sent its newest 500 and no way to ask for more, so this
        // is where the answer stops, not necessarily where he started.
        "That's as far back as this goes."
      ) : first !== null ? (
        `That's the beginning — the first thing he did was on ${longDate(first)}.`
      ) : (
        "That's the beginning."
      )}
    </div>
  );
}

/* ── One row ────────────────────────────────────────────────────────────── */

const TASK_STATE: Record<string, string> = {
  planning: "Waiting for your OK",
  approved: "Approved, up next",
  done: "Done",
  dropped: "Dropped",
};

interface View {
  icon: ReactNode;
  what: string;
  detail: string;
  live?: string;
  /** Named outputs of a folded job. */
  chips?: string[];
}

/** What a row says. Single events say what happened to the thing; runs say how many, and name
 *  them in the detail line. */
function describe(burst: Burst): View {
  const events = burst.events;
  const n = events.length;
  const newest = events[0]!;
  const oldest = events[n - 1]!;
  const firsts = () => events.map((event) => sentence(event.text, 80)[0]).join(" · ");

  switch (burst.kind) {
    case "output":
      if (n === 1) {
        return { icon: <FileText />, what: `Delivered “${newest.text}”`, detail: newest.on ? `for “${newest.on}”` : "" };
      }
      return {
        icon: <Layers />,
        what: `Finished ${n} things on “${newest.on ?? "a task"}”`,
        detail: `${oldest.text} through ${newest.text}, ${spanLabel(newest.ms - oldest.ms)}`,
        chips: events.slice(0, CHIPS).map((event) => event.text),
      };
    case "task": {
      if (n === 1) {
        return {
          icon: <ListChecks />,
          what: `Filed “${plain(newest.text)}”`,
          detail: TASK_STATE[newest.status ?? ""] ?? "",
          live: newest.status === "working" ? "Working" : undefined,
        };
      }
      const working = events.filter((event) => event.status === "working").length;
      return {
        icon: <ListChecks />,
        what: `Filed ${n} tasks`,
        detail: events.map((event) => plain(event.text)).join(" · "),
        live: working ? `${working} working` : undefined,
      };
    }
    case "memory": {
      if (n > 1) return { icon: <Brain />, what: `Remembered ${n} things`, detail: firsts() };
      const [head, rest] = sentence(newest.text);
      return { icon: <Brain />, what: `Remembered: ${head}`, detail: rest || (newest.level === "core" ? "Front of mind" : "In recall") };
    }
    case "journal": {
      if (n > 1) return { icon: <NotebookPen />, what: `Wrote ${n} journal entries`, detail: firsts() };
      const [head, rest] = sentence(newest.text, 90);
      return { icon: <NotebookPen />, what: `Wrote “${head}”`, detail: rest };
    }
    case "reminder": {
      if (n > 1) return { icon: <Bell />, what: `Set ${n} reminders`, detail: firsts() };
      const fires = newest.fireAt ? new Date(newest.fireAt).getTime() : 0;
      const due = fires ? `${longDate(fires)}, ${clock(fires)}` : "";
      return {
        icon: <Bell />,
        what: `Set a reminder: ${sentence(newest.text)[0]}`,
        detail: newest.status === "pending" ? `Fires ${due}` : due ? `For ${due}` : (newest.status ?? ""),
      };
    }
    case "asked":
      if (n > 1) return { icon: <MessageCircleQuestion />, what: `Asked you ${n} questions`, detail: firsts() };
      return { icon: <MessageCircleQuestion />, what: `Asked you: ${sentence(newest.text, 140)[0]}`, detail: "A question in one of your chats" };
    case "stopped":
      return { icon: <CirclePause />, what: n > 1 ? `Stopped ${n} sessions at their budget` : "Stopped a session at its budget", detail: sentence(newest.text)[0] };
  }
}

/** A run that holds different things (tasks, questions) opens by unfolding; anything else opens
 *  the thing — a job's outputs all live on its one task. */
const unfoldsToOpen = (burst: Burst) => burst.events.length > 1 && (burst.kind === "task" || burst.kind === "asked");

function expandable(burst: Burst): boolean {
  const n = burst.events.length;
  if (burst.kind === "output") return n > CHIPS;
  if (burst.kind === "task" || burst.kind === "asked") return n > 1;
  return n > 2;
}

function itemLabel(event: LifeEvent): string {
  return event.kind === "output" ? event.text : sentence(event.text, 90)[0];
}

function Row({
  burst,
  open,
  onToggle,
  opener,
}: {
  burst: Burst;
  open: boolean;
  onToggle: () => void;
  opener: (event: LifeEvent) => (() => void) | undefined;
}) {
  const view = describe(burst);
  const newest = burst.events[0]!;
  const n = burst.events.length;
  const canExpand = expandable(burst);
  const main = unfoldsToOpen(burst) ? onToggle : opener(newest);

  /* One button per row, stretched over it (`after:inset-0`), so the whole row is a target
     without a button inside a button; the row's own controls sit above it on `z-10`. */
  return (
    <div className="hover:bg-muted/60 relative flex min-h-9 shrink-0 items-start gap-3 rounded-[10px] px-2 py-1 transition-colors">
      <span className="text-muted-foreground/80 w-[60px] shrink-0 pt-2 text-right text-[11.5px] tabular-nums">{clock(newest.ms)}</span>
      <KindTile>{view.icon}</KindTile>
      <span className="flex min-w-0 flex-1 flex-col gap-[7px] pt-1.5">
        <span className="flex min-w-0 items-baseline gap-2.5 whitespace-nowrap">
          <span className="text-foreground min-w-0 max-w-[72%] shrink truncate text-[13px]">{view.what}</span>
          {view.live ? (
            <span className="text-roam flex shrink-0 items-center gap-[5px] text-[11.5px]">
              <span className="bg-roam size-1.5 rounded-full" />
              {view.live}
            </span>
          ) : null}
          <span className="text-muted-foreground/80 min-w-0 flex-1 truncate text-xs">{view.detail}</span>
          {canExpand && (open || !view.chips) ? (
            <button
              type="button"
              aria-expanded={open}
              onClick={onToggle}
              className="text-foreground/80 hover:text-foreground relative z-10 flex h-5 shrink-0 items-center gap-1 px-0.5 text-xs"
            >
              {open ? "Show less" : `Show all ${n}`}
              {open ? <ChevronUp className="size-3" /> : <ChevronDown className="size-3" />}
            </button>
          ) : null}
        </span>

        {view.chips && !open ? (
          <span className="flex min-w-0 items-center gap-1.5 pb-1">
            {/* Wrapping inside one fixed-height line: a chip that does not fit drops out whole
                rather than showing as half a pill. */}
            <span className="flex h-[22px] min-w-0 flex-wrap gap-1.5 overflow-hidden">
              {view.chips.map((chip, index) => (
                <Chip key={`${index}-${chip}`} className="max-w-[260px]">
                  <span className="truncate">{chip}</span>
                </Chip>
              ))}
            </span>
            {canExpand ? (
              <button
                type="button"
                aria-expanded={false}
                onClick={onToggle}
                className="text-foreground/80 hover:text-foreground relative z-10 flex h-[22px] shrink-0 items-center gap-1 px-1 text-xs whitespace-nowrap"
              >
                Show all {n}
                <ChevronDown className="size-3" />
              </button>
            ) : null}
          </span>
        ) : null}

        {open ? (
          <span className="grid max-w-[860px] grid-cols-2 gap-x-4 gap-y-0.5 pb-1.5">
            {burst.events.map((event) => {
              const go = opener(event);
              return (
                <button
                  key={event.key}
                  type="button"
                  onClick={go}
                  disabled={!go}
                  className="border-border/80 text-foreground/80 hover:bg-muted/50 hover:text-foreground relative z-10 flex h-7 min-w-0 items-center gap-2 rounded-[7px] border px-2 text-left text-[12.5px] transition-colors disabled:cursor-default"
                >
                  <span className="text-muted-foreground/80 flex shrink-0 [&_svg]:size-3">{ICON[event.kind]}</span>
                  <span className="min-w-0 flex-1 truncate">{itemLabel(event)}</span>
                  <span className="text-muted-foreground/80 shrink-0 text-[11px] tabular-nums">{clock(event.ms)}</span>
                </button>
              );
            })}
          </span>
        ) : null}
      </span>

      <span className="flex w-[250px] shrink-0 pt-[3px]">
        {burst.project ? (
          <Chip className="max-w-full min-w-0">
            <span className="truncate">{burst.project}</span>
          </Chip>
        ) : null}
      </span>

      <button
        type="button"
        onClick={main}
        disabled={!main}
        aria-label={unfoldsToOpen(burst) ? `${open ? "Fold" : "Show all"}: ${view.what}` : `Open: ${view.what}`}
        aria-expanded={unfoldsToOpen(burst) ? open : undefined}
        className="text-muted-foreground/80 focus-visible:after:ring-ring flex size-7 shrink-0 items-center justify-center rounded-[7px] outline-none after:absolute after:inset-0 after:rounded-[10px] after:content-[''] focus-visible:after:ring-2 disabled:cursor-default"
      >
        {unfoldsToOpen(burst) ? (
          open ? <ChevronUp className="size-3.5" /> : <ChevronDown className="size-3.5" />
        ) : (
          <ChevronRight className="size-3.5" />
        )}
      </button>
    </div>
  );
}

/* ── The 30-day strip ───────────────────────────────────────────────────── */

function ActivityStrip({ strip, onPick }: { strip: StripDay[]; onPick: (key: string) => void }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(1, ...strip.map((day) => day.count));
  const total = strip.reduce((sum, day) => sum + day.count, 0);
  const last = strip.length - 1;

  const tick = (day: StripDay, index: number) => {
    const date = new Date(day.midnight);
    if (index === last) return "Today";
    if (index === 0 || date.getDate() === 1) return `${date.getDate()} ${date.toLocaleDateString(undefined, { month: "short" })}`;
    if (index % 7 === 6 || index === hover) return String(date.getDate());
    return "";
  };

  return (
    <section aria-label="Last 30 days" className="bg-card border-border flex shrink-0 flex-col gap-2.5 rounded-[14px] border px-4 pt-3 pb-2.5">
      <div className="flex items-baseline gap-2.5">
        <span className="text-muted-foreground/80 text-[10.5px] font-semibold tracking-[0.1em] uppercase">Last 30 days</span>
        <span className="text-muted-foreground text-xs tabular-nums">{plural(total, "thing")}</span>
        <span className="flex-1" />
        <span className="text-muted-foreground/80 text-[11.5px]">Click a day to jump to it</span>
      </div>
      <div className="flex h-12 items-end gap-1.5">
        {strip.map((day, index) => {
          const height = day.count ? Math.max(3, Math.round((day.count / max) * 48)) : 2;
          return (
            <button
              key={day.key}
              type="button"
              aria-label={`${longDate(day.midnight)}${index === last ? " (today)" : ""}: ${plural(day.count, "thing")}`}
              onClick={() => onPick(day.key)}
              onMouseEnter={() => setHover(index)}
              onMouseLeave={() => setHover(null)}
              onFocus={() => setHover(index)}
              onBlur={() => setHover(null)}
              className="focus-visible:ring-ring relative flex h-12 min-w-0 flex-1 items-end rounded-[4px] p-0 outline-none focus-visible:ring-2"
            >
              <span
                className={cn(
                  "block w-full rounded-t-[4px] rounded-b-[1px] transition-colors",
                  index === last
                    ? "bg-foreground/80"
                    : index === hover
                      ? "bg-foreground/40"
                      : day.count
                        ? "bg-foreground/[0.17]"
                        : "bg-muted",
                )}
                style={{ height }}
              />
              {index === hover ? (
                <span
                  role="tooltip"
                  className={cn(
                    "bg-muted pointer-events-none absolute -top-1.5 z-30 flex w-max min-w-[214px] flex-col gap-0.5 rounded-lg border border-[color-mix(in_oklab,var(--foreground)_14%,transparent)] px-2.5 py-2 text-left shadow-[0_8px_24px_rgba(0,0,0,0.35)]",
                    // Near the right edge it opens leftwards, so it never runs off the card.
                    index > strip.length - 8 ? "right-[calc(100%+8px)]" : "left-[calc(100%+8px)]",
                  )}
                >
                  <span className="text-foreground text-xs font-semibold">{longDate(day.midnight)}</span>
                  <span className="text-foreground/80 text-xs tabular-nums">{plural(day.count, "thing")}</span>
                  {day.count ? (
                    <span className="text-muted-foreground/80 text-[11.5px] whitespace-nowrap tabular-nums">{breakdown(day.kinds)}</span>
                  ) : null}
                </span>
              ) : null}
            </button>
          );
        })}
      </div>
      <div aria-hidden className="flex h-[13px] gap-1.5">
        {strip.map((day, index) => (
          <span
            key={day.key}
            className={cn(
              "min-w-0 flex-1 text-center text-[10.5px] leading-[13px] whitespace-nowrap tabular-nums",
              index === last ? "text-foreground font-semibold" : index === hover ? "text-foreground" : "text-muted-foreground/80",
            )}
          >
            {tick(day, index)}
          </span>
        ))}
      </div>
    </section>
  );
}

/* ── Jump to date ───────────────────────────────────────────────────────── */

function JumpToDate({
  counts,
  oldest,
  going,
  unknown,
  onPick,
  className,
}: {
  className?: string;
  counts: ReadonlyMap<string, number>;
  oldest: number;
  /** A day being paged back to, `YYYY-MM-DD`, while its pages load. */
  going: string | null;
  /** Days older than anything loaded, whose counts are not known yet. */
  unknown?: (key: string) => boolean;
  onPick: (key: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [month, setMonth] = useState(() => monthIndex(Date.now()));
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (box.current && !box.current.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      // Claimed, as the date picker does: unclaimed, the panel's own Escape closes the panel.
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={box} className={cn("relative", className)}>
      <button
        type="button"
        aria-expanded={open}
        aria-busy={going ? true : undefined}
        onClick={() => {
          if (!open) setMonth(monthIndex(Date.now()));
          setOpen((was) => !was);
        }}
        className={cn(BUTTON.secondary, "h-9 gap-2 [&_svg]:size-3.5")}
      >
        <CalendarDays />
        {going ? `Going back to ${longDate(keyMidnight(going))}…` : "Jump to date"}
      </button>
      {open ? (
        <div className="absolute end-0 top-full z-40 mt-2 w-[280px]">
          <HeatCalendar
            month={month}
            onMonth={setMonth}
            counts={counts}
            selected={dayKey(Date.now())}
            onPick={(key) => {
              setOpen(false);
              onPick(key);
            }}
            min={monthIndex(oldest)}
            max={monthIndex(Date.now())}
            noun={["thing", "things"]}
            unknown={unknown}
            className="bg-popover shadow-[0_12px_32px_rgba(0,0,0,0.4)]"
          />
        </div>
      ) : null}
    </div>
  );
}
