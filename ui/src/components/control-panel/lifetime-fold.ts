/** Lifetime's model: the timeline as things he did, grouped by day, with the runs folded.
 *
 * The timeline endpoint is five tables merged by time — memories, journal, tasks, reminders and
 * the alerts he sent you — and read raw it is mostly the same line: a 54-screen job is 54 rows of
 * "Finished something on “The complete Pace mobile screen set”: …". So before anything is drawn,
 * each event says what it was (an output, a filed task, a question he asked you), and each day's
 * events fold:
 *
 *   - every output on the same task that day is one burst, wherever it falls in the day — a job
 *     interleaved with a memory and a journal entry is still one job;
 *   - otherwise, back-to-back events of the same kind on the same project are one burst
 *     ("Remembered 2 things").
 *
 * Pure, so the folding is tested on its own (`lifetime-fold.test.ts`) and the screen only draws. */
import type { BrainSnapshot, Message, TimelineEvent } from "@/lib/backend/brain";
import { dayKey, keyMidnight, midnight, msOf } from "./day-format";
import { projectMatcher } from "./journal-links";

export type LifeKind = "task" | "output" | "memory" | "journal" | "reminder" | "asked" | "stopped";
export type LifeFilter = "all" | "tasks" | "outputs" | "memory" | "journal" | "reminders";

/** Which kind each filter chip shows. "All" is everything, including the questions he asked
 *  and the sessions he stopped, which have no chip of their own. */
export const FILTER_KIND: Record<Exclude<LifeFilter, "all">, LifeKind> = {
  tasks: "task",
  outputs: "output",
  memory: "memory",
  journal: "journal",
  reminders: "reminder",
};

export interface LifeEvent {
  /** Unique and stable: kind and row id. */
  key: string;
  kind: LifeKind;
  ms: number;
  /** For an output, its title; a task, its goal; anything else, its text. */
  text: string;
  /** The project's name, or "" for none. */
  project: string;
  projectId?: number;
  /** The task a task event is, or the task an output was filed on. */
  taskId?: number;
  /** An output's task, as the alert names it. */
  on?: string;
  /** A task's or a reminder's status, as it stands now. */
  status?: string;
  /** A memory's level: core | recall. */
  level?: string;
  /** When a reminder fires, ISO. */
  fireAt?: string;
  /** Where a question or a stopped session lives — a chat. */
  link?: string;
}

/** "Finished something on “X”: Y" — the shape of every delivered-output alert. */
export const DELIVERED = /^Finished something on [“"](.+?)[”"]\s*:\s*(.+)$/s;
const ASKED = /^I need an answer before I can carry on:\s*/;

type Snapshot = Pick<BrainSnapshot, "tasks" | "projects" | "messages">;

/** The timeline, as things he did — newest first. Notes and reach-outs are dropped: they are the
 *  inbox's business, and a note to you is not something he did. */
export function toLifeEvents(timeline: readonly TimelineEvent[], snap: Snapshot): LifeEvent[] {
  const tasks = new Map(snap.tasks.map((task) => [task.id, task]));
  const projects = new Map(snap.projects.map((project) => [project.id, project]));
  const messages = new Map(snap.messages.map((message) => [message.id, message]));
  const taskByGoal = new Map(snap.tasks.map((task) => [task.goal.trim(), task.id]));
  const mentions = projectMatcher(snap.projects);

  const inProject = (projectId: number | null | undefined) => {
    const project = projectId != null ? projects.get(projectId) : undefined;
    return project ? { project: project.name, projectId: project.id } : { project: "" };
  };
  const ofTask = (taskId: number | undefined, fallback?: number | null) =>
    inProject((taskId != null ? tasks.get(taskId)?.project_id : null) ?? fallback);
  /* A memory or an entry belongs to nothing in the database, but he names the project he is
     writing about — the first one named is what the row is about. */
  const mentioned = (text: string) => {
    const [first] = mentions(text);
    return first ? { project: first.name, projectId: first.id } : { project: "" };
  };

  const out: LifeEvent[] = [];
  for (const event of timeline) {
    const id = typeof event.meta.id === "number" ? event.meta.id : undefined;
    const ms = msOf(event.at);
    const key = `${event.kind}:${id ?? event.at}`;

    if (event.kind === "message") {
      // The snapshot's row carries what the timeline's does not: its kind, its link, its project.
      const message = id != null ? (messages.get(id) as (Message & { project_id?: number | null }) | undefined) : undefined;
      const delivered = DELIVERED.exec(event.text);
      if (delivered) {
        const on = delivered[1]!.trim();
        const linked = message?.link?.startsWith("/tasks/") ? Number(message.link.slice(7)) : Number.NaN;
        const taskId = Number.isFinite(linked) ? linked : taskByGoal.get(on);
        out.push({
          key: `output:${id ?? event.at}`,
          kind: "output",
          ms,
          text: delivered[2]!.trim(),
          on,
          taskId,
          ...ofTask(taskId, message?.project_id),
        });
        continue;
      }
      const kind = message?.kind ?? (ASKED.test(event.text) ? "asked" : "");
      if (kind === "asked") {
        out.push({
          key,
          kind: "asked",
          ms,
          text: event.text.replace(ASKED, ""),
          link: message?.link ?? undefined,
          ...inProject(message?.project_id),
        });
      } else if (kind === "stuck") {
        out.push({ key, kind: "stopped", ms, text: event.text, link: message?.link ?? undefined, ...inProject(message?.project_id) });
      }
      continue;
    }
    if (event.kind === "task") {
      out.push({
        key,
        kind: "task",
        ms,
        text: event.text,
        taskId: id,
        status: String(event.meta.status ?? (id != null ? tasks.get(id)?.status : "") ?? ""),
        ...ofTask(id),
      });
      continue;
    }
    if (event.kind === "memory") {
      out.push({ key, kind: "memory", ms, text: event.text, level: String(event.meta.level ?? ""), ...mentioned(event.text) });
      continue;
    }
    if (event.kind === "journal") {
      out.push({ key, kind: "journal", ms, text: event.text, ...mentioned(event.text) });
      continue;
    }
    if (event.kind === "reminder") {
      out.push({
        key,
        kind: "reminder",
        ms,
        text: event.text,
        status: String(event.meta.status ?? ""),
        fireAt: typeof event.meta.fireAt === "string" ? event.meta.fireAt : undefined,
        ...mentioned(event.text),
      });
    }
  }
  return out.sort((a, b) => b.ms - a.ms);
}

/* ── Folding ────────────────────────────────────────────────────────────── */

/** One row on the screen: a single event, or a run of them. Events are newest first. */
export interface Burst {
  key: string;
  kind: LifeKind;
  project: string;
  events: LifeEvent[];
}

/** One day's events (newest first) as rows. See the module comment for the two rules. */
export function foldDay(events: readonly LifeEvent[]): Burst[] {
  const out: Burst[] = [];
  const jobs = new Map<string, Burst>();
  for (const event of events) {
    if (event.kind === "output") {
      const job = `output:${event.taskId ?? event.on ?? event.key}`;
      const burst = jobs.get(job);
      if (burst) {
        burst.events.push(event);
        continue;
      }
      const fresh: Burst = { key: job, kind: "output", project: event.project, events: [event] };
      jobs.set(job, fresh);
      out.push(fresh);
      continue;
    }
    // An output that joined a job further up was not drawn here, so it does not break a run.
    const last = out[out.length - 1];
    if (last && last.kind === event.kind && last.project === event.project) {
      last.events.push(event);
      continue;
    }
    out.push({ key: "", kind: event.kind, project: event.project, events: [event] });
  }
  // Keyed by the run's oldest event: new things arrive at the newest end, so the key — and
  // whether you had the row open — survives the run growing.
  for (const burst of out) {
    if (burst.kind !== "output") burst.key = `${burst.kind}:${burst.events[burst.events.length - 1]!.key}`;
  }
  return out;
}

export interface DayGroup {
  /** `YYYY-MM-DD`, local. */
  key: string;
  midnight: number;
  /** Events that day, before folding — what "76 things" counts. */
  count: number;
  bursts: Burst[];
}

/** Events grouped by local day, newest day first, each day folded. */
export function buildDays(events: readonly LifeEvent[]): DayGroup[] {
  const sorted = [...events].sort((a, b) => b.ms - a.ms);
  const days: { key: string; events: LifeEvent[] }[] = [];
  for (const event of sorted) {
    const key = dayKey(event.ms);
    const last = days[days.length - 1];
    if (last && last.key === key) last.events.push(event);
    else days.push({ key, events: [event] });
  }
  return days.map((day) => ({
    key: day.key,
    midnight: keyMidnight(day.key),
    count: day.events.length,
    bursts: foldDay(day.events),
  }));
}

/* ── The 30-day strip ───────────────────────────────────────────────────── */

export interface StripDay {
  key: string;
  midnight: number;
  count: number;
  kinds: Partial<Record<LifeKind, number>>;
}

/** The last `days` local days, oldest first and today last, with how much happened on each. */
export function activityStrip(events: readonly LifeEvent[], now = Date.now(), days = 30): StripDay[] {
  const today = new Date(midnight(now));
  const slots: StripDay[] = [];
  for (let back = days - 1; back >= 0; back--) {
    // Stepped by calendar day, not by 24 hours, so a daylight-saving night lands on its midnight.
    const day = new Date(today.getFullYear(), today.getMonth(), today.getDate() - back).getTime();
    slots.push({ key: dayKey(day), midnight: day, count: 0, kinds: {} });
  }
  const byKey = new Map(slots.map((slot) => [slot.key, slot]));
  for (const event of events) {
    const slot = byKey.get(dayKey(event.ms));
    if (!slot) continue;
    slot.count += 1;
    slot.kinds[event.kind] = (slot.kinds[event.kind] ?? 0) + 1;
  }
  return slots;
}

const KIND_WORDS: [LifeKind, string, string][] = [
  ["task", "task", "tasks"],
  ["output", "output", "outputs"],
  ["memory", "memory", "memories"],
  ["journal", "journal", "journal"],
  ["reminder", "reminder", "reminders"],
  ["asked", "question", "questions"],
  ["stopped", "stop", "stops"],
];

/** "46 tasks · 22 outputs · 4 memories · 4 journal". */
export function breakdown(kinds: Partial<Record<LifeKind, number>>): string {
  return KIND_WORDS.filter(([kind]) => kinds[kind])
    .map(([kind, one, many]) => `${kinds[kind]} ${kinds[kind] === 1 ? one : many}`)
    .join(" · ");
}

/** How long a run took, as a phrase that follows a comma: "in about a minute", "over 3 hours". */
export function spanLabel(ms: number): string {
  const seconds = Math.round(ms / 1000);
  if (seconds < 45) return "in under a minute";
  if (seconds < 90) return "in about a minute";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `in ${minutes} minutes`;
  const hours = Math.round(minutes / 60);
  return `over ${hours === 1 ? "an hour" : `${hours} hours`}`;
}
