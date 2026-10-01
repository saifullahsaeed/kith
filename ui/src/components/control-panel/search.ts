/** What the ⌘K palette searches, how it ranks what it finds, and what it offers when nothing does.
 *
 * Pure, and apart from the palette, for the same reason `format.ts` is: the palette is a dialog
 * with focus, keys and three queries in it, and none of that is what decides whether "ironline"
 * finds the Ironline project above a memory that mentions it in passing. That part is a function
 * of the snapshot and a string, so it is tested as one.
 *
 * Matching is substring, not fuzzy, on purpose. Every term has to appear somewhere in the thing —
 * its title, the line under it (project name, path, tags), or the text behind it — and where it
 * appears decides the rank: a title that starts with it beats a title with it in the middle, which
 * beats a path that has it. Fuzzy matching is what "did you mean" is for, and it is offered only
 * once a search has come back empty, so a typo costs one keypress rather than every search paying
 * for the possibility of one.
 */
import type { ConversationSummary } from "@/lib/backend";
import type {
  BrainSnapshot,
  JournalEntry,
  Memory,
  Project,
  Reminder,
  Source,
  Task,
  WorkspaceEntry,
} from "@/lib/backend/brain";
import { formatBytes } from "@/lib/bytes";
import { dayLabel, time, when } from "@/lib/dates";
import { shortPath } from "./format";

/* ── Kinds ──────────────────────────────────────────────────────────────── */

export type SearchKind = "project" | "memory" | "journal" | "task" | "chat" | "file" | "reminder" | "source";

/** The filter chips, in the board's order. Reminders and sources are searched too, but only show
 *  under All: the chip row is the design's, and two more chips for kinds that rarely match would
 *  crowd it for nothing. */
export const CHIP_KINDS = ["project", "memory", "journal", "task", "chat", "file"] as const;

/** Group order under All: the chips' order, then the two without a chip. */
export const KIND_ORDER: readonly SearchKind[] = [...CHIP_KINDS, "reminder", "source"];

export const KIND_CHIP: Record<SearchKind, string> = {
  project: "Projects",
  memory: "Memory",
  journal: "Journal",
  task: "Tasks",
  chat: "Chats",
  file: "Files",
  reminder: "Reminders",
  source: "Sources",
};

/** "11 more files", "1 more journal entry". */
const NOUN: Record<SearchKind, [string, string]> = {
  project: ["project", "projects"],
  memory: ["memory", "memories"],
  journal: ["journal entry", "journal entries"],
  task: ["task", "tasks"],
  chat: ["chat", "chats"],
  file: ["file", "files"],
  reminder: ["reminder", "reminders"],
  source: ["source", "sources"],
};

export function noun(kind: SearchKind, n: number): string {
  return `${n} ${NOUN[kind][n === 1 ? 0 : 1]}`;
}

/* ── Entries ────────────────────────────────────────────────────────────── */

/** A file or folder from a workspace listing, with where it sits. */
export interface FileItem {
  /** Relative to his workspace root — what the file viewer opens. */
  path: string;
  name: string;
  /** The folder it was listed in, "." for the root. */
  folder: string;
  type: "dir" | "file";
  size: number;
  modified: number;
}

/** Memory and journal rows carry their project; the server sends it and `brain.ts` doesn't
 *  declare it yet, so it is read as optional rather than assumed. */
type WithProject = { project_id?: number | null };

export type SearchItem =
  | { kind: "project"; value: Project }
  | { kind: "memory"; value: Memory & WithProject }
  | { kind: "journal"; value: JournalEntry & WithProject }
  | { kind: "task"; value: Task }
  | { kind: "chat"; value: ConversationSummary }
  | { kind: "file"; value: FileItem }
  | { kind: "reminder"; value: Reminder }
  | { kind: "source"; value: Source };

export type SearchEntry = SearchItem & {
  /** Unique across kinds: `memory:114`. */
  key: string;
  /** Shown before the title and not part of it: `#114 · `. */
  prefix: string;
  /** Line one, in full — the row cuts it to a snippet around the match. */
  title: string;
  /** Line two: project name, path, tags, status. Searched, and shown, so a match in it is seen. */
  meta: string;
  /** Searched but not on the row: a task's description, where a chat was left. */
  body: string;
  /** For ranking ties and for the empty query's "recent" order. Zero when unknown. */
  at: number;
  /** An exact-id match: typing "#262" or "262" finds task 262. */
  id: string;
  hay: { title: string; meta: string; body: string };
};

export const STATUS_LABEL: Record<string, string> = {
  planning: "Planning",
  approved: "Up next",
  working: "Working",
  done: "Done",
  dropped: "Dropped",
  active: "Active",
  paused: "Paused",
  archived: "Archived",
  pending: "Upcoming",
};

export function statusLabel(status: string): string {
  return STATUS_LABEL[status] ?? status.charAt(0).toUpperCase() + status.slice(1);
}

/** "Today, 09:17 AM" for today and yesterday, "Sep 30, 09:17 AM" before that. */
export function stamp(iso: string | null | undefined): string {
  if (!iso) return "";
  const day = dayLabel(iso);
  if (day === "Undated") return "";
  return day === "Today" || day === "Yesterday" ? `${day}, ${time(iso)}` : when(iso);
}

/** A project's name without its subtitle: "Ironline Haulage — operations portal" → "Ironline Haulage". */
export function shortName(name: string): string {
  return name.split(/ — | \(/)[0].trim() || name;
}

const flat = (text: string | null | undefined) => (text ?? "").replace(/\s+/g, " ").trim();
const ms = (iso: string | null | undefined) => {
  const at = iso ? Date.parse(iso) : NaN;
  return Number.isNaN(at) ? 0 : at;
};
const join = (...parts: (string | null | undefined | false)[]) => parts.filter(Boolean).join(" · ");

/** Listings the Files page skips too, and that no one is looking for by name. */
const NOISE = new Set([".DS_Store", ".git", "node_modules", "__pycache__", ".venv"]);

/**
 * Files as the listings have them: every folder's entries that has been fetched, keyed by the
 * folder's path. There is no server-side search of the workspace, so this is names only, in the
 * folders something has already listed — the root always, and whatever the Files page opened.
 */
export function filesFrom(listings: Record<string, WorkspaceEntry[]>): FileItem[] {
  const out: FileItem[] = [];
  for (const [folder, entries] of Object.entries(listings)) {
    for (const entry of entries) {
      if (NOISE.has(entry.name)) continue;
      const path = folder === "." || folder === "" ? entry.name : `${folder}/${entry.name}`;
      out.push({ path, name: entry.name, folder: folder || ".", type: entry.type, size: entry.size, modified: entry.modified });
    }
  }
  return out;
}

/** Where a folder is, for line two: `~/Kith/ironline-haulage/.kith`. */
export function folderLabel(folder: string, root: string): string {
  const base = root ? root.replace(/^\/Users\/[^/]+/, "~") : "His folder";
  const whole = folder === "." ? base : `${base}/${folder}`;
  return shortPath(whole, 56);
}

function entry(item: SearchItem, key: string, id: string, prefix: string, title: string, meta: string, body: string, at: number): SearchEntry {
  return {
    ...item,
    key,
    id,
    prefix,
    title,
    meta,
    body,
    at,
    hay: { title: title.toLowerCase(), meta: meta.toLowerCase(), body: body.toLowerCase() },
  } as SearchEntry;
}

/** Everything searchable, as rows. Built once per snapshot; a search is then a scan of strings. */
export function buildEntries(
  snap: BrainSnapshot | null,
  chats: ConversationSummary[] = [],
  files: FileItem[] = [],
  root = "",
): SearchEntry[] {
  const out: SearchEntry[] = [];
  const projects = new Map((snap?.projects ?? []).map((p) => [p.id, p]));
  const projectName = (id: number | null | undefined) => (id != null ? projects.get(id)?.name : undefined);

  if (snap) {
    const live = new Map<number, number>();
    for (const task of snap.tasks ?? []) {
      if (task.project_id != null && task.status !== "dropped") live.set(task.project_id, (live.get(task.project_id) ?? 0) + 1);
    }
    for (const p of snap.projects ?? []) {
      const n = live.get(p.id) ?? 0;
      const meta = join(statusLabel(p.status), `${n} task${n === 1 ? "" : "s"}`, p.directory ? shortPath(p.directory, 40) : null);
      out.push(entry({ kind: "project", value: p }, `project:${p.id}`, String(p.id), "", p.name, meta, flat(p.description), ms(p.updated_at)));
    }
    for (const m of snap.memories ?? []) {
      const tags = (m.tags ?? []).map((t) => `#${t}`).join(" ");
      out.push(entry({ kind: "memory", value: m }, `memory:${m.id}`, String(m.id), `#${m.id} · `, flat(m.content), join(stamp(m.created_at), tags), "", ms(m.created_at)));
    }
    for (const j of snap.journal ?? []) {
      out.push(entry({ kind: "journal", value: j }, `journal:${j.id}`, String(j.id), "", flat(j.entry), stamp(j.created_at), "", ms(j.created_at)));
    }
    for (const t of snap.tasks ?? []) {
      const outputs = t.outputs ? `${t.outputs} deliverable${t.outputs === 1 ? "" : "s"}` : null;
      const project = projectName(t.project_id);
      out.push(
        entry(
          { kind: "task", value: t },
          `task:${t.id}`,
          String(t.id),
          `#${t.id} `,
          flat(t.goal),
          join(statusLabel(t.status), outputs, project ? shortName(project) : null),
          flat(t.description),
          ms(t.updated_at),
        ),
      );
    }
    for (const r of snap.reminders ?? []) {
      const verb = r.status === "pending" ? "Fires" : r.status === "done" ? "Fired" : statusLabel(r.status);
      out.push(entry({ kind: "reminder", value: r }, `reminder:${r.id}`, String(r.id), "", flat(r.note), `${verb} ${stamp(r.fire_at)}`.trim(), "", ms(r.fire_at)));
    }
    for (const s of snap.sources ?? []) {
      out.push(
        entry(
          { kind: "source", value: s },
          `source:${s.id}`,
          String(s.id),
          "",
          flat(s.title),
          join(s.origin, s.chars ? `${s.chars.toLocaleString()} characters` : null),
          "",
          ms(s.created_at),
        ),
      );
    }
  }

  for (const c of chats) {
    const project = projectName(c.projectId);
    const meta = join(project ? `In ${shortName(project)}` : null, `${c.messages} message${c.messages === 1 ? "" : "s"}`, stamp(c.updatedAt));
    out.push(entry({ kind: "chat", value: c }, `chat:${c.id}`, c.id, "", flat(c.title), meta, flat(c.lastSaid), ms(c.updatedAt)));
  }

  for (const f of files) {
    const meta = join(folderLabel(f.folder, root), f.type === "dir" ? "folder" : formatBytes(f.size));
    out.push(entry({ kind: "file", value: f }, `file:${f.path}`, f.path, "", f.name, meta, "", f.modified * 1000));
  }

  return out;
}

/* ── Matching ───────────────────────────────────────────────────────────── */

/** The query as terms: lowercased, split on whitespace, each once. */
export function terms(query: string): string[] {
  return [...new Set(query.toLowerCase().split(/\s+/).filter(Boolean))];
}

const WORD = /[\p{L}\p{N}]/u;

/** Whether `term` occurs in `hay` at the start of a word, not only inside one. */
function atWordStart(hay: string, term: string): boolean {
  for (let i = hay.indexOf(term); i >= 0; i = hay.indexOf(term, i + 1)) {
    if (i === 0 || !WORD.test(hay[i - 1])) return true;
  }
  return false;
}

/** One field's say in the rank: 40 for starting with the term, 20 for a word that does, the base
 *  for having it anywhere. */
function field(hay: string, term: string, base: number): number {
  if (!hay.includes(term)) return 0;
  if (hay.startsWith(term)) return base + 40;
  return atWordStart(hay, term) ? base + 20 : base;
}

/** How well an entry answers the terms; 0 when any term is missing from it. */
export function score(e: SearchEntry, words: string[]): number {
  let total = 0;
  for (const word of words) {
    const bare = word.replace(/^#/, "");
    const best =
      bare === e.id.toLowerCase()
        ? 100
        : Math.max(field(e.hay.title, word, 60), field(e.hay.meta, word, 30), field(e.hay.body, word, 10));
    if (best === 0) return 0;
    total += best;
  }
  return total;
}

export interface SearchResult {
  /** Every hit, best first within each kind. */
  byKind: Record<SearchKind, SearchEntry[]>;
  counts: Record<SearchKind, number>;
  total: number;
}

const emptyByKind = (): Record<SearchKind, SearchEntry[]> => ({
  project: [],
  memory: [],
  journal: [],
  task: [],
  chat: [],
  file: [],
  reminder: [],
  source: [],
});

/**
 * The hits for a query, grouped by kind and ranked inside each group.
 *
 * An empty query is not "no results": it lists what is newest of each kind, so the palette opened
 * on nothing is already a way to jump back to the chat you just left.
 */
export function search(entries: SearchEntry[], query: string): SearchResult {
  const words = terms(query);
  const byKind = emptyByKind();
  if (words.length === 0) {
    for (const e of entries) byKind[e.kind].push(e);
    for (const kind of KIND_ORDER) byKind[kind].sort((a, b) => b.at - a.at);
  } else {
    const scored: [SearchEntry, number][] = [];
    for (const e of entries) {
      const s = score(e, words);
      if (s > 0) scored.push([e, s]);
    }
    scored.sort((a, b) => b[1] - a[1] || b[0].at - a[0].at);
    for (const [e] of scored) byKind[e.kind].push(e);
  }
  const counts = Object.fromEntries(KIND_ORDER.map((k) => [k, byKind[k].length])) as Record<SearchKind, number>;
  const total = KIND_ORDER.reduce((sum, k) => sum + counts[k], 0);
  return { byKind, counts, total };
}

export interface Group {
  kind: SearchKind;
  count: number;
  rows: SearchEntry[];
  /** What is left out under All: "11 more files". Empty when nothing is. */
  more: string;
}

/** How many of each kind All shows before saying how many more there are. */
export const ALL_CAP = 3;
/** One kind on its own shows this many — past it, the query wants narrowing, not scrolling. */
export const KIND_CAP = 60;

/** The groups a filter shows, in order, each cut to what fits. */
export function groups(result: SearchResult, filter: "all" | SearchKind): Group[] {
  const kinds = filter === "all" ? KIND_ORDER : [filter];
  const cap = filter === "all" ? ALL_CAP : KIND_CAP;
  return kinds
    .filter((kind) => result.byKind[kind].length > 0)
    .map((kind) => {
      const all = result.byKind[kind];
      const rows = all.slice(0, cap);
      const left = all.length - rows.length;
      return { kind, count: all.length, rows, more: left > 0 ? `${left} more ${NOUN[kind][left === 1 ? 0 : 1]}` : "" };
    });
}

/* ── Showing the match ──────────────────────────────────────────────────── */

export interface Piece {
  text: string;
  hit: boolean;
}

/** `text` cut into runs, the ones matching any term marked — every occurrence, not just the first. */
export function marks(text: string, words: string[]): Piece[] {
  if (!text) return [];
  const lower = text.toLowerCase();
  const ranges: [number, number][] = [];
  for (const word of words) {
    if (!word) continue;
    for (let i = lower.indexOf(word); i >= 0; i = lower.indexOf(word, i + word.length)) ranges.push([i, i + word.length]);
  }
  if (ranges.length === 0) return [{ text, hit: false }];
  ranges.sort((a, b) => a[0] - b[0]);
  const merged: [number, number][] = [];
  for (const range of ranges) {
    const last = merged[merged.length - 1];
    if (last && range[0] <= last[1]) last[1] = Math.max(last[1], range[1]);
    else merged.push([...range]);
  }
  const out: Piece[] = [];
  let at = 0;
  for (const [from, to] of merged) {
    if (from > at) out.push({ text: text.slice(at, from), hit: false });
    out.push({ text: text.slice(from, to), hit: true });
    at = to;
  }
  if (at < text.length) out.push({ text: text.slice(at), hit: false });
  return out;
}

/**
 * A one-line window onto long text that keeps the match on screen.
 *
 * A memory that says "ironline" two hundred characters in would otherwise show its first two
 * hundred characters and an ellipsis, which is the part that did not match. So the window starts a
 * few words before the first hit — at a word boundary, after an ellipsis — and the row's own
 * truncation takes care of the end.
 */
export function snippet(text: string, words: string[], lead = 30): string {
  const lower = text.toLowerCase();
  let first = -1;
  for (const word of words) {
    const i = word ? lower.indexOf(word) : -1;
    if (i >= 0 && (first < 0 || i < first)) first = i;
  }
  if (first <= lead + 12) return text;
  const space = text.lastIndexOf(" ", first - lead);
  const start = space >= 0 && space < first ? space + 1 : first - lead;
  return `…${text.slice(start)}`;
}

/* ── Did you mean ───────────────────────────────────────────────────────── */

/** Every word the index knows, with how often it appears — the dictionary "did you mean" uses. */
export function vocabulary(entries: SearchEntry[]): Map<string, number> {
  const words = new Map<string, number>();
  const pattern = /[\p{L}\p{N}][\p{L}\p{N}_-]*/gu;
  for (const e of entries) {
    for (const hay of [e.hay.title, e.hay.meta, e.hay.body]) {
      for (const match of hay.matchAll(pattern)) {
        const word = match[0];
        if (word.length < 3 || /^\d+$/.test(word)) continue;
        words.set(word, (words.get(word) ?? 0) + 1);
      }
    }
  }
  return words;
}

/**
 * Edit distance with transpositions counted as one edit (optimal string alignment), stopping
 * early once it is past `max`. A swapped pair is the commonest typo — "nuclaer" — and plain
 * Levenshtein scores it two, which would put it level with genuinely different words.
 */
export function editDistance(a: string, b: string, max = Infinity): number {
  if (Math.abs(a.length - b.length) > max) return max + 1;
  if (a === b) return 0;
  const m = b.length;
  let before = new Array<number>(m + 1).fill(0);
  let prev = Array.from({ length: m + 1 }, (_, j) => j);
  let cur = new Array<number>(m + 1).fill(0);
  for (let i = 1; i <= a.length; i++) {
    cur[0] = i;
    let low = i;
    for (let j = 1; j <= m; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      let v = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) v = Math.min(v, before[j - 2] + 1);
      cur[j] = v;
      if (v < low) low = v;
    }
    if (low > max) return max + 1;
    [before, prev, cur] = [prev, cur, before];
  }
  return prev[m];
}

/**
 * The query with its unmatched words swapped for the nearest known ones, or null when there is
 * nothing close enough to offer.
 *
 * Only words that match nothing are touched: in "ironl nuclaer", "ironl" already finds Ironline as
 * a substring, and "correcting" it to "iron" would be a worse search than the one typed.
 */
export function suggest(query: string, vocab: Map<string, number>, found: (word: string) => boolean): string | null {
  const words = terms(query);
  let changed = false;
  const out = words.map((word) => {
    if (word.length < 3 || vocab.has(word) || found(word)) return word;
    const limit = word.length <= 4 ? 1 : 2;
    let best = "";
    let bestDistance = limit + 1;
    let bestCount = 0;
    for (const [known, count] of vocab) {
      if (Math.abs(known.length - word.length) > limit) continue;
      const d = editDistance(word, known, limit);
      // Two edits away is only a typo if it starts the same: "kitten" is not a misspelling of
      // "written", though it is two edits from it. One edit away is close enough from anywhere.
      if (d === 2 && known[0] !== word[0]) continue;
      if (d < bestDistance || (d === bestDistance && count > bestCount)) {
        best = known;
        bestDistance = d;
        bestCount = count;
      }
    }
    if (!best || bestDistance > limit) return word;
    changed = true;
    return best;
  });
  return changed ? out.join(" ") : null;
}

/** Whether any entry contains `word` anywhere — the test "did you mean" uses to leave a word alone. */
export function anywhere(entries: SearchEntry[], word: string): boolean {
  return entries.some((e) => e.hay.title.includes(word) || e.hay.meta.includes(word) || e.hay.body.includes(word));
}

/** What an empty search looked through: "Searched all 709 journal entries." */
export function searchedLine(filter: "all" | SearchKind, totals: Record<SearchKind, number>): string {
  if (filter === "file") return `Searched the names of all ${noun("file", totals.file)}.`;
  if (filter !== "all") return `Searched all ${noun(filter, totals[filter])}.`;
  // Files last, because they alone were searched by name rather than by what they say.
  const parts = KIND_ORDER.filter((k) => k !== "file" && totals[k] > 0).map((k) => noun(k, totals[k]));
  if (totals.file > 0) parts.push(`the names of ${noun("file", totals.file)}`);
  if (parts.length === 0) return "There is nothing to search yet.";
  const list = parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}` : parts[0];
  return `Searched ${list}.`;
}
