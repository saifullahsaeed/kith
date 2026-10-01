/**
 * What the Projects list is made of, before any of it is drawn: one row per project (and one for
 * the "No project" tray), which tile it answers to, the order it sorts in, and which group it
 * sits under.
 *
 * Kept apart from the component because every one of these is a rule somebody will want to
 * check — "why is Sprout under Working and not Quiet", "why did placeholder fold away" — and a
 * rule buried in JSX can only be checked by looking at the screen.
 */
import type { BrainSnapshot } from "@/lib/backend/brain";
import type { ConversationSummary } from "@/lib/backend";
import { tally, type Tally } from "./format";
import { LOOSE, type ProjectRef } from "./types";

type Project = BrainSnapshot["projects"][number];
type Task = BrainSnapshot["tasks"][number];

/** The four summary tiles, each of which is also a filter on the list under it. */
export type Need = "waiting" | "working" | "next" | "done";
export type SortKey = "recent" | "waiting" | "name";

export const SORTS: readonly { value: SortKey; label: string }[] = [
  { value: "recent", label: "Most recently active" },
  { value: "waiting", label: "Most waiting on you" },
  { value: "name", label: "Name, A to Z" },
];

export interface Row {
  ref: ProjectRef;
  /** Absent for the "No project" tray, which is a bucket rather than a project. */
  project?: Project;
  name: string;
  /** One line of plain text: the list has no room for his Markdown, and a link inside a row
   *  that is itself a button would be a button inside a button. */
  description: string;
  directory: string | null;
  counts: Tally;
  tasks: Task[];
  /** Its conversations, newest first. */
  chats: ConversationSummary[];
  /** Epoch ms of the last thing that happened in it: the project changing, a task moving, or a
   *  conversation being added to — whichever is latest. */
  lastActive: number;
  /** Epoch ms its tasks last moved, 0 when it has none. */
  tasksMoved: number;
  /** No tasks at all, not even dropped ones, and no conversations: an empty board. These fold
   *  into one line rather than taking a row each to say nothing. */
  empty: boolean;
}

export const ms = (iso: string | undefined | null): number => {
  if (!iso) return 0;
  const at = new Date(iso).getTime();
  return Number.isNaN(at) ? 0 : at;
};

/** One line of plain text from his Markdown: no emphasis marks, code ticks, headings or link
 *  targets, and no line breaks. */
export function plainLine(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/[*_`#>]+/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * A project as the list reads it.
 *
 * `chatsKnown` is false while the conversations listing is still loading. Until it arrives a
 * project with no tasks is *not* called empty: folding it on a guess and then un-folding it a
 * moment later, when its conversations turn up, is a row jumping under your pointer.
 */
export function rowFor(
  project: Project | null,
  tasks: Task[],
  chats: ConversationSummary[],
  chatsKnown = true,
): Row {
  const sorted = [...chats].sort((a, b) => ms(b.updatedAt) - ms(a.updatedAt));
  const tasksMoved = tasks.reduce((latest, task) => Math.max(latest, ms(task.updated_at)), 0);
  const lastActive = Math.max(project ? ms(project.updated_at) : 0, tasksMoved, ms(sorted[0]?.updatedAt));
  return {
    ref: project ? project.id : LOOSE,
    project: project ?? undefined,
    name: project ? project.name : "No project",
    description: project
      ? plainLine(project.description ?? "")
      : "Loose work — errands that aren't part of anything bigger. Same board, no project.",
    directory: project ? project.directory : null,
    counts: tally(tasks),
    tasks,
    chats: sorted,
    lastActive,
    tasksMoved,
    empty: project != null && chatsKnown && tasks.length === 0 && sorted.length === 0,
  };
}

/** Whether a row answers to a tile: "Waiting on you" is every project with a plan waiting. */
export function meetsNeed(counts: Tally, need: Need | null): boolean {
  if (need === "waiting") return counts.planning > 0;
  if (need === "working") return counts.working > 0;
  if (need === "next") return counts.approved > 0;
  if (need === "done") return counts.done > 0;
  return true;
}

/** Sorted by `key`, ties broken by recency and then name. The tray always goes last: it is the
 *  bucket for whatever did not belong anywhere, and it read that way at the end of the old grid. */
export function sortRows(rows: Row[], key: SortKey): Row[] {
  const byName = (a: Row, b: Row) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
  const byRecent = (a: Row, b: Row) => b.lastActive - a.lastActive;
  return [...rows].sort((a, b) => {
    const loose = Number(a.ref === LOOSE) - Number(b.ref === LOOSE);
    if (loose) return loose;
    if (key === "name") return byName(a, b);
    if (key === "waiting") return b.counts.planning - a.counts.planning || byRecent(a, b) || byName(a, b);
    return byRecent(a, b) || byName(a, b);
  });
}

export type GroupKey = "needs" | "working" | "quiet" | "all";

export interface Group {
  key: GroupKey;
  rows: Row[];
}

/**
 * The list's groups, in the order the page shows them.
 *
 * Active projects split three ways by what they need: a plan waiting on you (Needs you), work in
 * progress and nothing to approve (Working), and everything else (Quiet). Paused, done and
 * archived projects are not asking for anything — a plan in an archived project waits until you
 * reopen it — so those tabs are one plain group.
 *
 * Empty boards come out of whichever group they would have joined and are returned apart, as
 * `folded`, for the page to show as one line. Rows keep the order they arrive in.
 */
export function groupRows(rows: Row[], byNeed: boolean): { groups: Group[]; folded: Row[] } {
  const folded = rows.filter((row) => row.empty);
  const rest = rows.filter((row) => !row.empty);
  if (!byNeed) return { groups: [{ key: "all", rows: rest }], folded };
  return {
    groups: [
      { key: "needs", rows: rest.filter((row) => row.counts.planning > 0) },
      { key: "working", rows: rest.filter((row) => row.counts.planning === 0 && row.counts.working > 0) },
      { key: "quiet", rows: rest.filter((row) => row.counts.planning === 0 && row.counts.working === 0) },
    ],
    folded,
  };
}

const midnight = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();

/** "Sep 22", with the year only when it is not this one. */
export function shortDate(at: number, now = Date.now(), locale?: string): string {
  if (!at) return "";
  const date = new Date(at);
  const sameYear = date.getFullYear() === new Date(now).getFullYear();
  return date.toLocaleDateString(locale, sameYear ? { month: "short", day: "numeric" } : { month: "short", day: "numeric", year: "numeric" });
}

/**
 * When something last happened, at the grain a list wants: "Today, 1:09 PM" while it is today
 * (the hour is the useful part then), "Yesterday", then a short date.
 */
export function whenLabel(at: number, now = Date.now(), locale?: string): string {
  if (!at) return "";
  const days = Math.round((midnight(new Date(now)) - midnight(new Date(at))) / 86_400_000);
  if (days <= 0) return `Today, ${new Date(at).toLocaleTimeString(locale, { hour: "numeric", minute: "2-digit" })}`;
  if (days === 1) return "Yesterday";
  return shortDate(at, now, locale);
}
