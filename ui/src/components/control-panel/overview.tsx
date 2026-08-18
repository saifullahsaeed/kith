import type { ReactNode } from "react";
import {
  BellRing,
  Brain,
  ChevronRight,
  Clock,
  FileText,
  FolderKanban,
  ListChecks,
  MessageCircle,
  NotebookPen,
  Repeat,
  Wrench,
} from "lucide-react";
import { Markdown, MarkdownInline } from "@/components/files";
import type { BrainSnapshot, TimelineEvent, TimelineKind } from "@/lib/backend/brain";
import { cn } from "@/lib/utils";
import { EmptyState, PageHeader, SectionLabel } from "./chrome";
import { groupByDay, time } from "@/lib/dates";
import { clamp, matches } from "./format";
import { CHIP, TAB_FOR } from "./types";
import type { Tab } from "./types";

const KIND_ICON: Record<TimelineKind, ReactNode> = {
  memory: <Brain className="size-3.5 text-violet-500" />,
  journal: <NotebookPen className="size-3.5 text-sky-500" />,
  task: <ListChecks className="size-3.5 text-emerald-500" />,
  reminder: <BellRing className="size-3.5 text-orange-500" />,
  message: <MessageCircle className="size-3.5 text-pink-500" />,
  tool: <Wrench className="size-3.5 text-rose-500" />,
};

/* ── Overview ───────────────────────────────────────────────────────────── */

type Stat = [string, number, ReactNode, keyof typeof CHIP];

export function Overview({
  snap,
  timeline,
  query,
  onNavigate,
}: {
  snap: BrainSnapshot;
  timeline: TimelineEvent[];
  query: string;
  onNavigate: (tab: Tab) => void;
}) {
  const c = snap.counts;
  // oxlint-disable react/jsx-key -- these icons are a tuple FIELD (Stat[2]: ReactNode),
  // destructured below as `icon` and rendered into one slot. They are never an array of
  // siblings, so React needs no key here; the two real list renders below do have keys.
  const groups: [string, Stat[]][] = [
    [
      "Mind",
      [
        ["Memories", c.memories, <Brain className="size-4" />, "violet"],
        ["Journal", c.journal, <NotebookPen className="size-4" />, "sky"],
      ],
    ],
    [
      "Doing",
      [
        ["Projects", c.projects, <FolderKanban className="size-4" />, "kith"],
        ["Tasks", c.tasks, <ListChecks className="size-4" />, "emerald"],
        ["Reminders", c.reminders, <BellRing className="size-4" />, "orange"],
        ["Schedules", c.schedules, <Repeat className="size-4" />, "orange"],
      ],
    ],
    [
      "Workspace",
      [
        ["Sources", c.sources, <FileText className="size-4" />, "sky"],
      ],
    ],
  ];
  const self = snap.self;
  const mood = snap.mood;
  const recent = timeline.filter((e) => matches(query, e.text)).slice(0, 14);
  return (
    <div className="space-y-10">
      {(self && (self.identity || self.profile)) || mood?.label ? (
        <div className="relative overflow-hidden rounded-xl border border-border/70 bg-gradient-to-br from-kith-soft/40 to-transparent p-6">
          <div className="relative">
            <div className="mb-3 flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
              <span className="kith-orb size-2" /> Who he's become
            </div>
            {self?.identity ? (
              <p className="text-xl font-semibold leading-snug tracking-tight">{self.identity}</p>
            ) : null}
            {self?.profile ? (
              <div className="mt-2.5 max-w-2xl text-sm leading-relaxed text-muted-foreground">
                {/* His own account of himself — his to format, so it renders. */}
                <Markdown>{self.profile}</Markdown>
              </div>
            ) : null}
            {!self?.identity && !self?.profile ? (
              <p className="text-sm text-muted-foreground">
                He hasn't shaped his own identity yet.
              </p>
            ) : null}
            {mood?.label ? (
              <div className="mt-5 flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-border/50 pt-4 text-sm">
                <span className="text-muted-foreground">Currently feeling</span>
                <span className="font-semibold text-kith">{mood.label}</span>
                <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <span className="h-1.5 w-28 overflow-hidden rounded-full bg-muted">
                    <span
                      className="block h-full rounded-full bg-kith transition-all"
                      style={{ width: `${clamp(mood.energy)}%` }}
                    />
                  </span>
                  energy {mood.energy}
                </span>
                {mood.note ? (
                  <span className="truncate text-xs text-muted-foreground/80">
                    · <MarkdownInline>{mood.note}</MarkdownInline>
                  </span>
                ) : null}
              </div>
            ) : null}
          </div>
        </div>
      ) : null}

      <div className="space-y-6">
        {groups.map(([label, stats]) => (
          <div key={label}>
            <SectionLabel>{label}</SectionLabel>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
              {stats.map(([name, n, icon, color]) => (
                <button
                  key={name}
                  onClick={() => onNavigate(TAB_FOR[name])}
                  className="group flex items-center gap-3 rounded-xl border border-border/70 bg-card/50 p-3.5 text-left transition-all hover:border-kith/40 hover:bg-card/80"
                >
                  <span
                    className={cn(
                      "flex size-9 shrink-0 items-center justify-center rounded-lg",
                      CHIP[color],
                    )}
                  >
                    {icon}
                  </span>
                  <span className="min-w-0">
                    <span className="block text-lg font-semibold tabular-nums leading-none">
                      {n ?? 0}
                    </span>
                    <span className="mt-1 block truncate text-xs text-muted-foreground">
                      {name}
                    </span>
                  </span>
                  <ChevronRight className="ml-auto size-4 shrink-0 text-muted-foreground/0 transition-colors group-hover:text-muted-foreground/60" />
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>

      <div>
        <SectionLabel hint="his last steps">Recent activity</SectionLabel>
        {recent.length === 0 ? (
          <EmptyState icon={<Clock className="size-5" />}>Nothing recent.</EmptyState>
        ) : (
          <div className="rounded-xl border border-border/70 bg-card/40 p-5">
            <TimelineList events={recent} />
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Lifetime (a proper vertical timeline with a spine) ─────────────────── */

export function Lifetime({ events }: { events: TimelineEvent[] }) {
  if (events.length === 0) {
    return (
      <>
        <PageHeader
          icon={<Clock className="size-5" />}
          color="kith"
          title="Lifetime"
          subtitle="Everything he's done, newest first."
        />
        <EmptyState icon={<Clock className="size-5" />}>Nothing here yet.</EmptyState>
      </>
    );
  }
  const groups = groupByDay(events, (e) => e.at);
  return (
    <>
      <PageHeader
        icon={<Clock className="size-5" />}
        color="kith"
        title="Lifetime"
        count={events.length}
        subtitle="Everything he's done, newest first."
      />
      <div className="space-y-8">
        {groups.map(([day, items]) => (
          <div key={day}>
            <div className="sticky top-0 z-10 -mx-1 mb-3 bg-background/80 px-1 py-1 text-xs font-semibold text-muted-foreground backdrop-blur-sm">
              {day}
            </div>
            <TimelineList events={items} />
          </div>
        ))}
      </div>
    </>
  );
}

/** The connected timeline used by both Overview and Lifetime. */
function TimelineList({ events }: { events: TimelineEvent[] }) {
  return (
    <ul className="relative space-y-4 before:absolute before:top-1 before:bottom-1 before:left-[11px] before:w-px before:bg-border">
      {events.map((e, i) => (
        <li key={i} className="relative flex gap-3 pl-0">
          <span className="z-10 mt-px flex size-6 shrink-0 items-center justify-center rounded-full border border-border bg-background shadow-sm">
            {KIND_ICON[e.kind]}
          </span>
          <div className="min-w-0 flex-1 pt-0.5">
            <span className="break-words text-sm leading-relaxed">
              <MarkdownInline>{e.text}</MarkdownInline>
            </span>
            <span className="ml-2 text-[11px] tabular-nums text-muted-foreground">
              {time(e.at)}
            </span>
          </div>
        </li>
      ))}
    </ul>
  );
}
