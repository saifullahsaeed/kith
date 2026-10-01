/**
 * Today: what needs you, what he is doing, and what changed — the panel's first screen.
 *
 * It replaced "Overview", which was seven count tiles and the last fourteen timeline lines. The
 * tiles answered "how much is in there", which nobody opens the panel to ask; the timeline was
 * fourteen copies of "Finished something on …" in a row. Nothing on it said that 63 plans were
 * waiting on you, or that he had stopped a session at its budget — the two things you would want
 * to know first.
 *
 * So the screen is three questions, in the order you would ask them, drawn from the snapshot and
 * the timeline the panel already holds (no request of its own):
 *
 *   1. Needs you — a session he stopped, and every plan waiting for your OK, grouped by project.
 *   2. Working now — the tasks he is on, with how far through each one's checklist he is.
 *   3. What changed — today's events, with runs of the same kind of thing folded into one row.
 */
import { useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router";
import {
  Bell,
  BookOpen,
  Brain,
  Check,
  ChevronRight,
  CirclePause,
  ListChecks,
  MessageCircleQuestion,
  NotebookPen,
  Repeat,
} from "lucide-react";

import type { BrainSnapshot, Message, Task, TimelineEvent } from "@/lib/backend/brain";
import { time } from "@/lib/dates";
import { pathForSettings } from "@/lib/router";
import { cn } from "@/lib/utils";
import { BUTTON, Card, CardHead, Chip, Empty, KindTile, Label, PageTitle, Segmented } from "./chrome";
import { matches } from "./format";
import type { ProjectRef, Tab } from "./types";

type Window = "today" | "week";
type Lens = "all" | "work" | "mind";

const DAY = 86_400_000;

/** Local midnight, `days` ago. */
function since(days: number): number {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() - days * DAY;
}

const at = (iso: string) => {
  const ms = new Date(iso).getTime();
  return Number.isNaN(ms) ? 0 : ms;
};

/** One line of plain text from his markdown: no emphasis marks, no code ticks, no headings. */
function plain(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/[*_`#>]+/g, "")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
}

/** The first sentence, or the first `max` characters, whichever is shorter. */
function firstSentence(text: string, max = 140): string {
  const flat = plain(text);
  const end = flat.search(/[.!?](\s|$)/);
  const cut = end > 20 && end < max ? flat.slice(0, end + 1) : flat.slice(0, max);
  return cut.length < flat.length && !/[.!?]$/.test(cut) ? `${cut.trimEnd()}…` : cut;
}

export function Today({
  snap,
  timeline,
  query,
  onNavigate,
  onOpenProject,
  onOpenTask,
  create,
}: {
  snap: BrainSnapshot;
  timeline: TimelineEvent[];
  query: string;
  onNavigate: (tab: Tab) => void;
  onOpenProject: (ref: ProjectRef) => void;
  onOpenTask: (id: number) => void;
  create: (kind: string, data: Record<string, unknown>) => Promise<void>;
}) {
  const [window, setWindow] = useState<Window>("today");
  const from = since(window === "today" ? 0 : 6);
  const date = new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });

  return (
    <div className="flex flex-col gap-[22px]">
      <PageTitle
        title="Today"
        subtitle={`${date} — what's waiting on you, what he's on, and what changed ${window === "today" ? "since this morning" : "this week"}.`}
      >
        <Segmented
          label="How far back"
          value={window}
          onChange={setWindow}
          options={[
            { value: "today", label: "Today" },
            { value: "week", label: "This week" },
          ]}
        />
      </PageTitle>

      <div className="grid h-[402px] grid-cols-[1.3fr_1.1fr_0.8fr] gap-4">
        <NeedsYou snap={snap} from={from} onNavigate={onNavigate} onOpenProject={onOpenProject} onOpenTask={onOpenTask} />
        <WorkingNow snap={snap} onNavigate={onNavigate} onOpenProject={onOpenProject} onOpenTask={onOpenTask} />
        <ComingUp snap={snap} onNavigate={onNavigate} create={create} />
      </div>

      <div className="grid min-h-[440px] grid-cols-[2.2fr_1fr] gap-4">
        <WhatChanged snap={snap} timeline={timeline} from={from} query={query} onNavigate={onNavigate} onOpenTask={onOpenTask} />
        <MindToday snap={snap} from={from} onNavigate={onNavigate} />
      </div>
    </div>
  );
}

/* ── Needs you ──────────────────────────────────────────────────────────── */

/** A row that opens something: 46px, title over a quiet line, something on the right. */
function RowLink({
  title,
  sub,
  right,
  onClick,
  highlight,
}: {
  title: ReactNode;
  sub?: ReactNode;
  right?: ReactNode;
  onClick: () => void;
  highlight?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex h-[46px] w-full items-center gap-2.5 rounded-[10px] px-2 text-left transition-colors",
        highlight ? "bg-kith/[0.08] hover:bg-kith/[0.12]" : "hover:bg-muted/50",
      )}
    >
      <span className="flex min-w-0 flex-1 flex-col gap-px">
        <span className="text-foreground truncate text-[13px]">{title}</span>
        {sub ? <span className="text-muted-foreground/80 truncate text-[11.5px]">{sub}</span> : null}
      </span>
      {right}
      <ChevronRight className="text-muted-foreground/80 size-3.5 shrink-0" />
    </button>
  );
}

function NeedsYou({
  snap,
  from,
  onNavigate,
  onOpenProject,
  onOpenTask,
}: {
  snap: BrainSnapshot;
  from: number;
  onNavigate: (tab: Tab) => void;
  onOpenProject: (ref: ProjectRef) => void;
  onOpenTask: (id: number) => void;
}) {
  const navigate = useNavigate();
  const projects = useMemo(() => new Map(snap.projects.map((p) => [p.id, p])), [snap.projects]);

  /* A session he stopped at its budget is the one alert that leaves work unfinished until you
     act. Only today's: the alerts log keeps them forever, and last week's stop is not news. */
  const stopped: Message[] = useMemo(
    () =>
      snap.messages
        .filter((m) => m.kind === "stuck" && at(m.created_at) >= Math.max(from, since(0)))
        .sort((a, b) => at(b.created_at) - at(a.created_at))
        .slice(0, 1),
    [snap.messages, from],
  );

  const plans = snap.tasks.filter((t) => t.status === "planning");
  const open = (task: Task) => {
    const project = task.project_id != null ? projects.get(task.project_id) : undefined;
    return !project || project.status === "active";
  };
  const live = plans.filter(open);
  const parked = plans.length - live.length;

  /* Grouped by project, most waiting first: "Sadeef One · 27 plans" is one decision about where
     to spend an hour, where twenty-seven rows would be a list to wade through. A project with a
     single plan shows the plan itself — that row *is* the decision. */
  const groups = useMemo(() => {
    const by = new Map<number | "none", Task[]>();
    for (const task of live) {
      const key = task.project_id ?? "none";
      by.set(key, [...(by.get(key) ?? []), task]);
    }
    return [...by.entries()].sort((a, b) => b[1].length - a[1].length).slice(0, 5);
  }, [live]);

  const count = plans.length + stopped.length;

  return (
    <Card tone="attention" label="Needs you">
      <CardHead
        tone="attention"
        dot="attention"
        count={count}
        action={
          <button type="button" onClick={() => onNavigate("projects")} className="text-kith/85 hover:text-kith text-xs">
            Review all plans
          </button>
        }
      >
        Needs you
      </CardHead>

      {stopped.map((message) => (
        <div key={message.id} className="border-border bg-card mx-3 mt-3 mb-1 flex items-start gap-3 rounded-[10px] border p-3">
          <KindTile tone="attention">
            <CirclePause />
          </KindTile>
          <div className="flex min-w-0 flex-1 flex-col gap-[3px]">
            <div className="flex items-baseline gap-2">
              <span className="text-[13px] font-semibold">He stopped a session at its budget</span>
              <span className="text-muted-foreground/80 text-[11px]">{time(message.created_at)}</span>
            </div>
            <span className="text-foreground/80 line-clamp-2 text-[12.5px] leading-[1.45]">{firstSentence(message.body, 200)}</span>
            <div className="mt-2 flex gap-2">
              <button
                type="button"
                className={cn(BUTTON.primary, BUTTON.small)}
                onClick={() => (message.link ? navigate(message.link) : onNavigate("projects"))}
              >
                Carry on
              </button>
              <button type="button" className={cn(BUTTON.secondary, BUTTON.small)} onClick={() => navigate(pathForSettings("budget"))}>
                Raise the budget
              </button>
            </div>
          </div>
        </div>
      ))}

      <Label count={plans.length} className="px-4 pt-3 pb-1.5">
        Plans waiting for your OK
      </Label>
      {groups.length === 0 ? (
        <div className="px-4 pb-4">
          <Empty title="Nothing to approve">Every plan he filed has an answer.</Empty>
        </div>
      ) : (
        <div className="flex flex-col gap-0.5 px-2">
          {groups.map(([key, tasks]) => {
            const project = key === "none" ? undefined : projects.get(key);
            const name = project?.name ?? "No project";
            if (tasks.length === 1) {
              const task = tasks[0]!;
              return (
                <RowLink
                  key={String(key)}
                  highlight
                  title={task.goal}
                  sub={`${name} · #${task.id}${task.priority === "high" ? " · high priority" : ""}`}
                  right={<Chip tone="attention">Review</Chip>}
                  onClick={() => onOpenTask(task.id)}
                />
              );
            }
            const oldest = tasks.reduce((a, b) => (at(a.created_at) < at(b.created_at) ? a : b));
            return (
              <RowLink
                key={String(key)}
                title={name}
                sub={`Oldest waiting since ${new Date(oldest.created_at).toLocaleDateString(undefined, { day: "numeric", month: "short" })}`}
                right={<Chip tone="attention">{tasks.length} plans</Chip>}
                onClick={() => onOpenProject(key === "none" ? "none" : key)}
              />
            );
          })}
        </div>
      )}
      {parked ? (
        <div className="border-kith/10 text-muted-foreground/80 mt-auto border-t px-4 py-2.5 text-[11.5px]">
          {parked} more {parked === 1 ? "is" : "are"} in paused or archived projects — they stay out of the way until you reopen one.
        </div>
      ) : null}
    </Card>
  );
}

/* ── Working now ────────────────────────────────────────────────────────── */

function WorkingNow({
  snap,
  onNavigate,
  onOpenProject,
  onOpenTask,
}: {
  snap: BrainSnapshot;
  onNavigate: (tab: Tab) => void;
  onOpenProject: (ref: ProjectRef) => void;
  onOpenTask: (id: number) => void;
}) {
  const working = snap.tasks.filter((t) => t.status === "working");
  const names = useMemo(() => new Map(snap.projects.map((p) => [p.id, p.name])), [snap.projects]);

  /* The project he has most going on is shown task by task, with how far each checklist has got;
     the rest are one line each. Five rows of one project read as progress; five rows from five
     projects read as noise. */
  const [lead, rest] = useMemo(() => {
    const by = new Map<number | "none", Task[]>();
    for (const task of working) {
      const key = task.project_id ?? "none";
      by.set(key, [...(by.get(key) ?? []), task]);
    }
    const sorted = [...by.entries()].sort((a, b) => b[1].length - a[1].length);
    return [sorted[0], sorted.slice(1, 4)] as const;
  }, [working]);

  const rank = { high: 0, normal: 1, low: 2 } as Record<string, number>;

  return (
    <Card label="Working now">
      <CardHead
        dot={working.length ? "live" : undefined}
        count={`${working.length} task${working.length === 1 ? "" : "s"}`}
        action={
          <button type="button" onClick={() => onNavigate("projects")} className="text-foreground/80 hover:text-foreground text-xs">
            All projects
          </button>
        }
      >
        Working now
      </CardHead>
      {!lead ? (
        <div className="p-4">
          <Empty title="He isn't on anything">Nothing is in Working on any board. Approve a plan to give him something.</Empty>
        </div>
      ) : (
        <>
          <Label className="px-4 pt-2.5 pb-1">{lead[0] === "none" ? "No project" : (names.get(lead[0]) ?? "Project")}</Label>
          <div className="flex flex-col gap-0.5 px-2">
            {[...lead[1]]
              .sort((a, b) => (rank[a.priority] ?? 1) - (rank[b.priority] ?? 1) || a.id - b.id)
              .slice(0, 5)
              .map((task) => {
                const done = task.checklist_done ?? 0;
                const total = task.checklist_total ?? 0;
                return (
                  <button
                    key={task.id}
                    type="button"
                    onClick={() => onOpenTask(task.id)}
                    className="hover:bg-muted/50 flex h-12 w-full items-center gap-2.5 rounded-[10px] px-2 text-left transition-colors"
                  >
                    <span className="text-muted-foreground/80 w-[34px] shrink-0 font-mono text-[11px]">#{task.id}</span>
                    <span className="flex min-w-0 flex-1 flex-col gap-1.5">
                      <span className="truncate text-[13px]">{task.goal}</span>
                      {/* No checklist, no bar: an empty track reads as "0% done", which is a claim. */}
                      {total ? (
                        <span className="bg-muted flex h-[3px] overflow-hidden rounded-full">
                          <span className="bg-roam rounded-full" style={{ width: `${(done / total) * 100}%` }} />
                        </span>
                      ) : null}
                    </span>
                    <span className="text-muted-foreground w-8 shrink-0 text-right text-[11.5px] tabular-nums">
                      {total ? `${done}/${total}` : ""}
                    </span>
                  </button>
                );
              })}
          </div>
          {rest.length ? (
            <>
              <Label className="px-4 pt-3 pb-1">Elsewhere</Label>
              <div className="flex flex-col gap-0.5 px-2 pb-3">
                {rest.map(([key, tasks]) => (
                  <button
                    key={String(key)}
                    type="button"
                    onClick={() => onOpenProject(key === "none" ? "none" : key)}
                    className="text-foreground/80 hover:bg-muted/50 flex h-[34px] w-full items-center gap-2.5 rounded-lg px-2 text-left text-[13px] transition-colors"
                  >
                    <span className="bg-roam size-1.5 shrink-0 rounded-full" />
                    <span className="min-w-0 flex-1 truncate">{key === "none" ? "No project" : (names.get(key) ?? "Project")}</span>
                    <span className="text-muted-foreground/80 text-[11.5px] tabular-nums">{tasks.length} working</span>
                  </button>
                ))}
              </div>
            </>
          ) : null}
        </>
      )}
    </Card>
  );
}

/* ── Coming up ──────────────────────────────────────────────────────────── */

/** Minutes from now until a wall-clock time today (or tomorrow, once it has passed). */
function minutesUntil(hour: number, minute = 0, dayOffset = 0): number {
  const now = new Date();
  const then = new Date(now.getFullYear(), now.getMonth(), now.getDate() + dayOffset, hour, minute);
  if (then.getTime() <= now.getTime()) then.setDate(then.getDate() + 1);
  return Math.max(1, Math.round((then.getTime() - now.getTime()) / 60_000));
}

function ComingUp({
  snap,
  onNavigate,
  create,
}: {
  snap: BrainSnapshot;
  onNavigate: (tab: Tab) => void;
  create: (kind: string, data: Record<string, unknown>) => Promise<void>;
}) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const pending = snap.reminders
    .filter((r) => r.status === "pending")
    .sort((a, b) => at(a.fire_at) - at(b.fire_at));
  const fired = snap.reminders.length - pending.length;
  const standing = snap.schedules.filter((s) => s.status === "active");

  /* The three times people actually pick. The old composer took minutes only — "in 30 min" —
     which made "tomorrow at nine" an arithmetic problem. */
  const choices = [
    { label: "In 30 min", minutes: () => 30 },
    { label: "Tonight", minutes: () => minutesUntil(21) },
    { label: "Tomorrow 9:00", minutes: () => minutesUntil(9, 0, 1) },
  ];
  const remind = async (minutes: number) => {
    const text = note.trim();
    if (!text || busy) return;
    setBusy(true);
    try {
      await create("reminder", { note: text, in_minutes: minutes });
      setNote("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card label="Coming up">
      <CardHead
        action={
          <button type="button" onClick={() => onNavigate("reminders")} className="text-foreground/80 hover:text-foreground text-xs">
            Reminders
          </button>
        }
      >
        Coming up
      </CardHead>
      <div className="flex min-h-0 flex-1 flex-col gap-3.5 p-4">
        {pending.length === 0 ? (
          <Empty title="Nothing is due">
            No reminders are waiting to fire.{fired ? ` The last ${fired} have all fired.` : ""}
          </Empty>
        ) : (
          <div className="flex flex-col gap-1">
            {pending.slice(0, 3).map((reminder) => (
              <div key={reminder.id} className="bg-muted/50 flex flex-col gap-0.5 rounded-[10px] px-3 py-2">
                <span className="line-clamp-2 text-[12.5px]">{plain(reminder.note)}</span>
                <span className="text-kith text-[11.5px]">{reminder.fires}</span>
              </div>
            ))}
          </div>
        )}
        <form
          className="flex flex-col gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void remind(30);
          }}
        >
          <label htmlFor="today-remind" className="text-muted-foreground/80 text-[10.5px] font-semibold tracking-[0.1em] uppercase">
            Remind him
          </label>
          <input
            id="today-remind"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Check the build and report…"
            className="border-input bg-background placeholder:text-muted-foreground/80 focus-visible:border-ring h-[38px] rounded-[10px] border px-3 text-[13px] outline-none"
          />
          <div className="flex flex-wrap gap-1.5">
            {choices.map((choice, index) => (
              <button
                key={choice.label}
                type="button"
                disabled={!note.trim() || busy}
                onClick={() => void remind(choice.minutes())}
                className={cn(
                  "h-7 rounded-[7px] border px-2.5 text-xs transition-colors disabled:opacity-60",
                  index === 0
                    ? "border-input bg-muted/70 text-foreground hover:bg-muted"
                    : "border-border text-muted-foreground hover:text-foreground",
                )}
              >
                {choice.label}
              </button>
            ))}
          </div>
        </form>
        <div className="border-border/80 mt-auto flex items-center gap-2.5 border-t pt-3">
          <KindTile>
            <Repeat />
          </KindTile>
          <span className="flex min-w-0 flex-1 flex-col gap-px">
            <span className="text-foreground/80 truncate text-[12.5px]">
              {standing.length ? `${standing.length} standing job${standing.length === 1 ? "" : "s"}` : "No standing jobs"}
            </span>
            <span className="text-muted-foreground/80 truncate text-[11.5px]">
              {standing[0] ? `Next: ${standing[0].fires}` : "Things he should do every day or hour"}
            </span>
          </span>
          <button type="button" onClick={() => onNavigate("schedules")} className="text-foreground/80 hover:text-foreground text-xs">
            {standing.length ? "Open" : "Add"}
          </button>
        </div>
      </div>
    </Card>
  );
}

/* ── What changed ───────────────────────────────────────────────────────── */

interface Change {
  key: string;
  at: string;
  icon: ReactNode;
  what: string;
  detail: string;
  project: string;
  chips?: string[];
  attention?: boolean;
  lens: Exclude<Lens, "all">;
  open?: () => void;
}

/** "Finished something on “X”: Y" — the shape of every delivered-output alert. */
const DELIVERED = /^Finished something on [“"](.+?)[”"]\s*:\s*(.+)$/s;

function WhatChanged({
  snap,
  timeline,
  from,
  query,
  onNavigate,
  onOpenTask,
}: {
  snap: BrainSnapshot;
  timeline: TimelineEvent[];
  from: number;
  query: string;
  onNavigate: (tab: Tab) => void;
  onOpenTask: (id: number) => void;
}) {
  const [lens, setLens] = useState<Lens>("all");
  const navigate = useNavigate();

  const changes = useMemo(() => {
    const tasks = new Map(snap.tasks.map((t) => [t.id, t]));
    const projects = new Map(snap.projects.map((p) => [p.id, p.name]));
    const messages = new Map(snap.messages.map((m) => [m.id, m]));
    const projectOfTask = (id: number | undefined) => {
      const task = id != null ? tasks.get(id) : undefined;
      return task?.project_id != null ? (projects.get(task.project_id) ?? "") : "";
    };

    const events = timeline
      .filter((e) => at(e.at) >= from)
      .filter((e) => matches(query, e.text))
      .sort((a, b) => at(b.at) - at(a.at));

    const out: Change[] = [];
    for (const event of events) {
      const id = typeof event.meta.id === "number" ? event.meta.id : undefined;
      if (event.kind === "message") {
        const message = id != null ? messages.get(id) : undefined;
        const taskId = message?.link?.startsWith("/tasks/") ? Number(message.link.slice(7)) : undefined;
        const delivered = DELIVERED.exec(event.text);
        if (delivered) {
          const [, on, what] = delivered;
          /* A run of outputs on the same task is one row: "Finished 54 screens on …". Fourteen
             identical lines in a row was what the old Overview opened on. */
          const last = out[out.length - 1];
          if (last && last.key.startsWith("delivered:") && last.key.slice(10) === on) {
            last.chips = [...(last.chips ?? []), what!.trim()];
            continue;
          }
          out.push({
            key: `delivered:${on}`,
            at: event.at,
            icon: <Check />,
            what: `Finished ${what!.trim()}`,
            detail: `On “${on}”`,
            project: projectOfTask(taskId),
            chips: [what!.trim()],
            lens: "work",
            open: taskId ? () => onOpenTask(taskId) : undefined,
          });
          continue;
        }
        if (message?.kind === "stuck") {
          out.push({
            key: `m${id}`,
            at: event.at,
            icon: <CirclePause />,
            what: "Stopped a session at its budget",
            detail: "Waiting on you to carry on or raise the budget",
            project: "Session",
            attention: true,
            lens: "work",
            open: message.link ? () => navigate(message.link!) : undefined,
          });
          continue;
        }
        if (message?.kind === "asked") {
          out.push({
            key: `m${id}`,
            at: event.at,
            icon: <MessageCircleQuestion />,
            what: firstSentence(event.text.replace(/^I need an answer before I can carry on:\s*/, "Asked you: ")),
            detail: "A question in one of your chats",
            project: "",
            lens: "work",
            open: message.link ? () => navigate(message.link!) : undefined,
          });
          continue;
        }
        continue; // notes and reach-outs are the alerts log's business, not the day's story
      }
      if (event.kind === "task") {
        const task = id != null ? tasks.get(id) : undefined;
        const status = String(event.meta.status ?? task?.status ?? "");
        const verb =
          status === "done" ? "Finished" : status === "working" ? "Started" : status === "planning" ? "Filed a plan" : "Updated";
        out.push({
          key: `t${id}:${event.at}`,
          at: event.at,
          icon: <ListChecks />,
          what: `${verb}: ${plain(event.text)}`,
          detail: status === "planning" ? "Planning · waiting for your OK" : status === "done" ? "Done" : status,
          project: projectOfTask(id),
          lens: "work",
          open: id != null ? () => onOpenTask(id) : undefined,
        });
        continue;
      }
      if (event.kind === "memory") {
        out.push({
          key: `mem${id}`,
          at: event.at,
          icon: <Brain />,
          what: `Remembered: ${firstSentence(event.text, 110)}`,
          detail: event.meta.level === "core" ? "Front of mind" : "In recall",
          project: "",
          lens: "mind",
          open: () => onNavigate("memory"),
        });
        continue;
      }
      if (event.kind === "journal") {
        out.push({
          key: `j${id}`,
          at: event.at,
          icon: <NotebookPen />,
          what: "Wrote in his journal",
          detail: firstSentence(event.text, 160),
          project: "",
          lens: "mind",
          open: () => onNavigate("journal"),
        });
        continue;
      }
      if (event.kind === "reminder") {
        out.push({
          key: `r${id}`,
          at: event.at,
          icon: <Bell />,
          what: `Reminder: ${firstSentence(event.text, 110)}`,
          detail: String(event.meta.status ?? ""),
          project: "",
          lens: "work",
          open: () => onNavigate("reminders"),
        });
      }
    }
    // A folded run says how many, and names the first few.
    for (const change of out) {
      if (change.chips && change.chips.length > 1) {
        const n = change.chips.length;
        change.what = `Finished ${n} things on “${change.key.slice(10)}”`;
        change.detail = "Each one filed as an output on that task";
        const shown = change.chips.slice(0, 4);
        change.chips = n > 4 ? [...shown, `+ ${n - 4} more`] : shown;
      } else {
        change.chips = undefined;
      }
    }
    return out;
  }, [timeline, from, query, snap, onNavigate, onOpenTask, navigate]);

  const shown = changes.filter((c) => lens === "all" || c.lens === lens).slice(0, 7);

  return (
    <Card label="What changed">
      <CardHead
        action={
          <Segmented
            size="sm"
            label="Show"
            value={lens}
            onChange={setLens}
            options={[
              { value: "all", label: "All" },
              { value: "work", label: "Work" },
              { value: "mind", label: "Mind" },
            ]}
          />
        }
      >
        What changed {from >= since(0) ? "today" : "this week"}
      </CardHead>
      <div className="flex flex-col gap-px px-2 py-1.5">
        {shown.length === 0 ? (
          <div className="p-2">
            <Empty title="A quiet stretch">{query ? "Nothing here matches your search." : "Nothing has happened yet in this window."}</Empty>
          </div>
        ) : (
          shown.map((change) => (
            <button
              key={change.key}
              type="button"
              onClick={change.open}
              disabled={!change.open}
              className={cn(
                "flex min-h-12 w-full items-start gap-3 rounded-[10px] px-2 py-1.5 text-left transition-colors",
                change.attention ? "bg-kith/[0.06] hover:bg-kith/10" : "hover:bg-muted/40",
                !change.open && "cursor-default",
              )}
            >
              <span className="text-muted-foreground/80 w-16 shrink-0 pt-1.5 text-right text-[11.5px] tabular-nums">{time(change.at)}</span>
              <KindTile>{change.icon}</KindTile>
              <span className="flex min-w-0 flex-1 flex-col gap-1 pt-[5px]">
                <span className="text-foreground truncate text-[13px]">{change.what}</span>
                <span className="text-muted-foreground/80 truncate text-xs">{change.detail}</span>
                {change.chips ? (
                  <span className="mt-0.5 flex flex-wrap gap-1.5">
                    {change.chips.map((chip) => (
                      <Chip key={chip}>{chip}</Chip>
                    ))}
                  </span>
                ) : null}
              </span>
              <span className="text-muted-foreground max-w-[180px] shrink-0 truncate pt-1.5 text-[11.5px]">{change.project}</span>
            </button>
          ))
        )}
      </div>
      <button
        type="button"
        onClick={() => onNavigate("lifetime")}
        className="border-border/80 text-foreground/80 hover:text-foreground mt-auto flex h-10 shrink-0 items-center border-t px-4 text-left text-xs"
      >
        Everything he's done is in Lifetime
      </button>
    </Card>
  );
}

/* ── His mind today ─────────────────────────────────────────────────────── */

function MindToday({ snap, from, onNavigate }: { snap: BrainSnapshot; from: number; onNavigate: (tab: Tab) => void }) {
  const remembered = snap.memories.filter((m) => at(m.created_at) >= from);
  const entries = snap.journal.filter((j) => at(j.created_at) >= from);
  const core = snap.memories.filter((m) => m.level === "core").length;
  const newest = [...(remembered.length ? remembered : snap.memories)].sort((a, b) => at(b.created_at) - at(a.created_at)).slice(0, 2);
  const latest = [...snap.journal].sort((a, b) => at(b.created_at) - at(a.created_at))[0];

  const stat = (n: number, label: string) => (
    <div className="border-border/80 bg-background flex flex-col gap-0.5 rounded-[10px] border px-3 py-2.5">
      <span className="text-xl font-[650] tabular-nums">{n}</span>
      <span className="text-muted-foreground/80 text-[11.5px]">{label}</span>
    </div>
  );

  return (
    <Card label="His mind">
      <CardHead
        action={
          <button type="button" onClick={() => onNavigate("memory")} className="text-foreground/80 hover:text-foreground text-xs">
            Memory
          </button>
        }
      >
        His mind {from >= since(0) ? "today" : "this week"}
      </CardHead>
      <div className="grid grid-cols-3 gap-2 px-4 pt-3.5 pb-1.5">
        {stat(remembered.length, "remembered")}
        {stat(entries.length, entries.length === 1 ? "journal entry" : "journal entries")}
        {stat(core, "front of mind")}
      </div>
      <Label className="px-4 pt-3 pb-1.5">{remembered.length ? "New memories" : "Latest memories"}</Label>
      <div className="flex flex-col gap-2 px-4">
        {newest.map((memory, index) => (
          <button
            key={memory.id}
            type="button"
            onClick={() => onNavigate("memory")}
            className={cn(
              "flex flex-col gap-1.5 rounded-[10px] px-3 py-2.5 text-left transition-colors",
              index === 0 ? "bg-muted/60 hover:bg-muted" : "border-border/80 hover:bg-muted/40 border",
            )}
          >
            <span className="line-clamp-2 text-[12.5px] leading-[1.45]">{plain(memory.content)}</span>
            <span className="text-muted-foreground/80 flex gap-1.5 text-[11px]">
              {memory.tags.slice(0, 3).map((tag) => (
                <span key={tag}>#{tag}</span>
              ))}
              <span className="flex-1" />
              <span>{time(memory.created_at)}</span>
            </span>
          </button>
        ))}
      </div>
      {latest ? (
        <>
          <Label className="px-4 pt-3.5 pb-1.5">Latest in his journal</Label>
          <button
            type="button"
            onClick={() => onNavigate("journal")}
            className="border-border/80 text-foreground/80 hover:bg-muted/40 mx-4 mb-4 line-clamp-3 rounded-[10px] border px-3 py-2.5 text-left text-[12.5px] leading-normal italic transition-colors"
          >
            {firstSentence(latest.entry, 260)}
          </button>
        </>
      ) : (
        <div className="px-4 pt-3.5 pb-4">
          <Empty>
            <span className="inline-flex items-center gap-1.5">
              <BookOpen className="size-3.5" /> His journal is empty.
            </span>
          </Empty>
        </div>
      )}
    </Card>
  );
}
