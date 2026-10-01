/**
 * The project page's decisions, apart from its drawing: which column a card moves to, which
 * conversations a filter keeps, what kind of thing an output is, what a transcript says you
 * asked for. Pure, so each one is tested on its own rather than through three tabs of UI.
 */
import type { ConversationSummary, StoredTurn } from "@/lib/backend/conversations";
import type { ProjectOutput } from "@/lib/backend/brain";
import type { SharedBoard } from "@/lib/backend/shared-board";
import { looksLikeADesign } from "@/lib/design/detect";

/* ── Dates, the way the boards write them ──────────────────────────────────
 *
 * "Sep 22", not "September 22" and not "Monday". `dayLabel` names the weekday for the last six
 * days, which reads well in a feed grouped by day and badly in a column of rows, where "Monday"
 * beside "Sep 18" asks you to work out which came first. */

const DAY = 86_400_000;

function midnight(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

/** Today, Yesterday, or "Sep 22" — with the year only when it is not this one. */
export function shortDay(iso: string, now = new Date()): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  const days = Math.round((midnight(now) - midnight(at)) / DAY);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  return at.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    ...(at.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }),
  });
}

/** "1:09 PM". */
export function clock(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  return at.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

/** A row's date: the time when it was today, the day otherwise. "Today, 1:09 PM" / "Sep 23". */
export function rowWhen(iso: string, now = new Date()): string {
  const day = shortDay(iso, now);
  return day === "Today" ? `Today, ${clock(iso)}` : day;
}

/** The day *and* the time, for a detail pane where both are wanted. "Today, 9:40 AM". */
export function dayAndClock(iso: string, now = new Date()): string {
  const day = shortDay(iso, now);
  return day ? `${day}, ${clock(iso)}` : "";
}

/* ── The board's keyboard move ──────────────────────────────────────────── */

/** The columns in the order work moves through them. Dropped sits past Done, and only exists
 *  while it is being shown — a key that moves a card into a column you cannot see is a card
 *  that vanished. */
const LANES = ["planning", "approved", "working", "done"] as const;

export const LANE_TITLE: Record<string, string> = {
  planning: "Planning",
  approved: "Up next",
  working: "Working",
  done: "Done",
  dropped: "Dropped",
};

export type MoveTarget =
  | { status: string }
  /** There is a column that way, and it does not take cards from you. */
  | { refused: string };

/**
 * Where ← or → sends a card: one column over, by the same rule the drag follows.
 *
 * Working refuses, exactly as it refuses a drop — it drives the busy light and the "working now"
 * markers, so a card arriving there announces work he never started. The key does not skip over
 * it to Done either: "one column" is the promise the hint makes, and a key that sometimes jumps
 * two is one you stop trusting. Null is the edge of the board.
 */
export function moveTarget(status: string, direction: -1 | 1, showDropped: boolean): MoveTarget | null {
  const lanes: string[] = showDropped ? [...LANES, "dropped"] : [...LANES];
  const at = lanes.indexOf(status);
  if (at < 0) return null;
  const next = lanes[at + direction];
  if (!next) return null;
  if (next === "working") return { refused: "Only he moves a card into Working" };
  return { status: next };
}

/* ── Conversations ──────────────────────────────────────────────────────── */

export type ChatFilter = "all" | "working" | "waiting";

/** Waiting on you outranks working: it is the one of the two that needs something from you. */
export function chatState(one: ConversationSummary): "working" | "waiting" | "idle" {
  if (one.waiting) return "waiting";
  if (one.working) return "working";
  return "idle";
}

export function filterChats(items: ConversationSummary[], filter: ChatFilter, text: string): ConversationSummary[] {
  const q = text.trim().toLowerCase();
  return items.filter((one) => {
    if (filter !== "all" && chatState(one) !== filter) return false;
    if (!q) return true;
    return one.title.toLowerCase().includes(q) || one.lastSaid.toLowerCase().includes(q);
  });
}

/** A title that is a pasted link reads as a link — set in mono, as the board draws it. */
export function looksLikeALink(title: string): boolean {
  return /^<?(https?:\/\/|[a-z0-9-]+\.[a-z]{2,}\/)/i.test(title.trim());
}

/** The text of a stored turn's prose, joined. */
function proseOf(turn: StoredTurn): string {
  return turn.parts
    .flatMap((part) => (part.kind === "text" ? [part.text] : []))
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();
}

/** "ok", "hey", "keep going" — a turn of the conversation, not a thing anybody asked for. */
const SHORT_REPLY =
  /^(ok(ay)?|k|yes|yeah|yep|no|nope|hey|hi|hello|thanks?( you)?|thx|ty|nice|great|cool|good|perfect|sure|go( on| ahead)?|continue|keep going|carry on|do it|done|proceed|next)[\s.!?]*$/i;

/**
 * What you asked for in a conversation, in order — the turns you typed, minus the ones that only
 * kept it moving. Counted rather than silently dropped, so the list does not pass for the whole
 * conversation. Harness turns (`system`) are not yours and are not here.
 */
export function whatYouAsked(timeline: StoredTurn[]): { asks: { at: string; text: string }[]; left: number } {
  const asks: { at: string; text: string }[] = [];
  let left = 0;
  for (const turn of timeline) {
    if (turn.role !== "user") continue;
    const text = proseOf(turn);
    if (!text) continue;
    if (SHORT_REPLY.test(text) || text.length < 4) {
      left += 1;
      continue;
    }
    asks.push({ at: turn.at ?? "", text });
  }
  return { asks, left };
}

/**
 * The files he wrote in a conversation, newest first, once each — read off the write and edit
 * calls the transcript already holds. Only what the page of transcript in hand covers: the
 * caller says so when the page is not the whole conversation.
 */
export function filesWritten(timeline: StoredTurn[]): { path: string; at: string }[] {
  const seen = new Map<string, string>();
  for (const turn of timeline) {
    if (turn.role !== "assistant") continue;
    for (const part of turn.parts) {
      if (part.kind !== "tool") continue;
      const args = part.arguments ?? {};
      const paths: unknown[] =
        part.name === "write_file" || part.name === "edit_file"
          ? [args.path]
          : part.name === "edit_files" && Array.isArray(args.edits)
            ? (args.edits as Record<string, unknown>[]).map((edit) => edit?.path ?? args.path)
            : [];
      for (const path of paths) {
        if (typeof path !== "string" || !path) continue;
        // Re-inserted so the map's order is "last written", which is the order the list wants.
        seen.delete(path);
        seen.set(path, turn.at ?? "");
      }
    }
  }
  return [...seen.entries()].map(([path, at]) => ({ path, at })).reverse();
}

/** A path as it reads inside its project: relative to the folder when it is in it, `~` for home. */
export function insideProject(path: string, directory: string | null | undefined): string {
  if (directory) {
    const root = directory.replace(/\/+$/, "");
    if (path.startsWith(`${root}/`)) return path.slice(root.length + 1);
  }
  return homeShort(path);
}

/** `/Users/me/…` → `~/…`. */
export function homeShort(path: string): string {
  return path.replace(/^\/Users\/[^/]+/, "~");
}

/* ── Outputs ────────────────────────────────────────────────────────────── */

export type OutputKind = "design" | "file" | "link" | "note";
export type OutputFilter = "all" | OutputKind;
export type OutputSort = "newest" | "oldest" | "name" | "largest";

/** A design is an HTML file the editor can open; the server only knows "file", so this decides. */
export function outputKind(one: Pick<ProjectOutput, "kind" | "content">): OutputKind {
  if (one.kind === "link") return "link";
  if (one.kind === "text") return "note";
  return looksLikeADesign(one.kind, one.content) ? "design" : "file";
}

/** When the output last changed: the file's own time when there is one, the filing otherwise. */
export function outputTime(one: Pick<ProjectOutput, "modified" | "created_at">): string {
  return one.modified ?? one.created_at;
}

const stamp = (iso: string) => {
  const ms = new Date(iso).getTime();
  return Number.isNaN(ms) ? 0 : ms;
};

/**
 * Sorted, stably: ties keep the server's order, which is newest filed first.
 *
 * Newest means newest *filed*, not newest written. The file's own time moves whenever anything
 * touches it — a formatter, a checkout — and sorting by it put the design system he filed last
 * behind fifty screens he filed before it.
 */
export function sortOutputs(items: ProjectOutput[], sort: OutputSort): ProjectOutput[] {
  const indexed = items.map((one, at) => ({ one, at }));
  const by: Record<OutputSort, (a: ProjectOutput, b: ProjectOutput) => number> = {
    newest: (a, b) => stamp(b.created_at) - stamp(a.created_at),
    oldest: (a, b) => stamp(a.created_at) - stamp(b.created_at),
    name: (a, b) => a.title.localeCompare(b.title, undefined, { numeric: true, sensitivity: "base" }),
    largest: (a, b) => (b.bytes ?? -1) - (a.bytes ?? -1),
  };
  return indexed.sort((x, y) => by[sort](x.one, y.one) || x.at - y.at).map(({ one }) => one);
}

/** `.kith/design/brief.md` → `MD`. The tile's label; `FILE` when there is no extension. */
export function extensionLabel(path: string): string {
  const name = path.split("/").pop() ?? "";
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toUpperCase().slice(0, 5) : "FILE";
}

export function fileName(path: string): string {
  return path.split("/").pop() || path;
}

/** `https://www.example.com/a/b` → `example.com`. The link itself when it is not a URL. */
export function linkDomain(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/* ── The shared folder ──────────────────────────────────────────────────── */

/**
 * The banner's one line: a headline and the counts after it.
 *
 * Null when there is nothing to say, which is the common case — the banner draws nothing then.
 * `blocked` is its own headline, because nothing was read and the reason is the whole message.
 */
export function sharedSummary(
  state: SharedBoard | null,
): { headline: string; detail: string; waiting: number; blocked: boolean } | null {
  if (!state || !state.folder) return null;
  if (state.blocked) {
    return { headline: "This project's shared folder can't be read", detail: state.blocked, waiting: 0, blocked: true };
  }
  const waiting = state.added.length + state.updated.length;
  if (!waiting && !state.kept.length) return null;
  const parts = [
    state.added.length ? `${state.added.length} new from someone else` : "",
    state.updated.length ? `${state.updated.length} newer in the folder` : "",
    state.kept.length ? `${state.kept.length} newer here` : "",
  ].filter(Boolean);
  return {
    headline: waiting
      ? `${waiting} task${waiting === 1 ? "" : "s"} in the shared folder ${waiting === 1 ? "isn't" : "aren't"} on this board`
      : "The shared folder differs from this board",
    detail: parts.join(", "),
    waiting,
    blocked: false,
  };
}

/* ── Text ───────────────────────────────────────────────────────────────── */

/** One paragraph of plain text from his markdown, for a clamped line where markup would show. */
export function plainText(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/[*_~>]+/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

/** Split on backticks, so `code` can be set in mono and the rest left as prose. */
export function codeRuns(text: string): { code: boolean; text: string }[] {
  return text
    .split(/(`[^`]+`)/g)
    .filter(Boolean)
    .map((run) => (run.startsWith("`") && run.endsWith("`") && run.length > 2 ? { code: true, text: run.slice(1, -1) } : { code: false, text: run }));
}
