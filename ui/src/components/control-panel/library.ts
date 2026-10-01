/** The LIBRARY screens' arithmetic — Sources and Files — kept apart from the components so it can
 *  be tested without rendering anything. */

/* ── Sources ────────────────────────────────────────────────────────────── */

export type SourceKind = "link" | "text" | "file";

/**
 * What a source is, from the two things the list knows about it.
 *
 * The server stores one `origin` per source: the URL for a link, the word `pasted` for anything
 * that arrived as text. A dropped file arrives as text too — the endpoint takes no bytes — so the
 * Sources screen sends the file's name as its title, and a title that *is* a file name is how a
 * file is told apart from a paste afterwards. Nothing else names a source like `api.md`.
 */
export function sourceKind(source: { origin: string; title: string }): SourceKind {
  if (/^https?:\/\//i.test(source.origin)) return "link";
  return /^[^\s/\\]+\.[a-z0-9]{1,8}$/i.test(source.title.trim()) ? "file" : "text";
}

/** `https://www.docs.github.com/en/x?y` → `docs.github.com`. The origin itself when it is not a URL. */
export function domainOf(origin: string): string {
  try {
    return new URL(origin).hostname.replace(/^www\./, "");
  } catch {
    return origin;
  }
}

/** A URL as it reads in a line of text: no scheme, no query, no trailing slash. */
export function bareUrl(origin: string): string {
  try {
    const url = new URL(origin);
    const path = url.pathname.replace(/\/$/, "");
    return `${url.hostname.replace(/^www\./, "")}${path}`;
  } catch {
    return origin;
  }
}

/* The passages he recalls by.
 *
 * `services/sources.py` splits every source into overlapping windows of 1,200 characters, 150 of
 * them shared with the window before, ending each one on a paragraph or a sentence where it can —
 * and each window is embedded on its own, so a question pulls in the passage that matches rather
 * than the whole page. The reader's gutter numbers those passages, which means this has to be the
 * same split, character for character: a gutter that drew its own seams would be numbering
 * something he never stored. `library.test.ts` checks it against the server's function.
 *
 * Counted in code points, as Python counts, not UTF-16 units — an emoji in a page would otherwise
 * shift every seam after it by one. */
const CHUNK_SIZE = 1200;
const CHUNK_OVERLAP = 150;

/** Last index of `needle` wholly inside `cps[from, to)`, relative to `from`; -1 when absent. */
function lastIndexIn(cps: string[], from: number, to: number, needle: string): number {
  const want = Array.from(needle);
  for (let at = to - want.length; at >= from; at--) {
    let hit = true;
    for (let k = 0; k < want.length; k++) {
      if (cps[at + k] !== want[k]) {
        hit = false;
        break;
      }
    }
    if (hit) return at - from;
  }
  return -1;
}

/** The windows `_chunks` keeps, as [start, stop) code-point offsets into the trimmed text. */
export function passageWindows(content: string): { start: number; stop: number }[] {
  const cps = Array.from(content.trim());
  const n = cps.length;
  if (n <= CHUNK_SIZE) return n ? [{ start: 0, stop: n }] : [];
  const out: { start: number; stop: number }[] = [];
  let start = 0;
  while (start < n) {
    let end = start + CHUNK_SIZE;
    let stop = Math.min(end, n);
    if (end < n) {
      const cut = Math.max(
        lastIndexIn(cps, start, stop, "\n\n"),
        lastIndexIn(cps, start, stop, ". "),
      );
      if (cut > CHUNK_SIZE / 2) {
        end = start + cut + 1;
        stop = end;
      }
    }
    // `_chunks` drops a window that strips to nothing, so it is not a passage and not counted.
    if (cps.slice(start, stop).join("").trim()) out.push({ start, stop });
    start = end - CHUNK_OVERLAP;
  }
  return out;
}

export interface Passage {
  /** Its number among the stored passages, from 1. */
  n: number;
  /** The text this passage adds — what it holds beyond the overlap with the one before. */
  text: string;
}

/**
 * The stored text, cut where each passage's new text begins.
 *
 * The windows overlap, so laying them end to end would print 150 characters twice at every seam.
 * Each passage instead shows only what it adds: from where the previous window stopped to where
 * this one stops. A window ends on a paragraph or a sentence, so that is also where each block of
 * the reader begins. The very last window can be all overlap — `_chunks` keeps it anyway — and it
 * is counted in the total but has nothing of its own to show.
 */
export function passagesOf(content: string): { passages: Passage[]; total: number } {
  const cps = Array.from(content.trim());
  const windows = passageWindows(content);
  const passages: Passage[] = [];
  let from = 0;
  windows.forEach((w, i) => {
    const text = cps.slice(from, w.stop).join("").trim();
    from = Math.max(from, w.stop);
    if (text) passages.push({ n: i + 1, text });
  });
  return { passages, total: windows.length };
}

/** A passage's text as blocks to draw: fenced code, markdown headings, and plain paragraphs. */
export type Block = { type: "code" | "heading" | "para"; text: string };

export function blocksOf(text: string): Block[] {
  const blocks: Block[] = [];
  // A fence first, so a blank line inside one does not split it into paragraphs.
  const parts = text.split(/(```[\s\S]*?(?:```|$))/);
  for (const part of parts) {
    if (!part.trim()) continue;
    if (part.startsWith("```")) {
      blocks.push({ type: "code", text: part.replace(/^```[^\n]*\n?/, "").replace(/\n?```$/, "") });
      continue;
    }
    for (const para of part.split(/\n\s*\n/)) {
      const line = para.trim();
      if (!line) continue;
      const heading = /^#{1,6}\s+(.+)$/.exec(line);
      if (heading && !line.includes("\n")) blocks.push({ type: "heading", text: heading[1] });
      else blocks.push({ type: "para", text: line });
    }
  }
  return blocks;
}

/**
 * What the composer should hold after "Ask about this": what you had typed, a blank line, then
 * `About source #12 ("Title"): ` for you to finish. Null when it already names this source —
 * clicking twice is a slip, not a request to ask twice.
 */
export function askLine(held: string, source: { id: number; title: string }): string | null {
  const mark = `source #${source.id}`;
  if (new RegExp(`${mark}(?!\\d)`).test(held)) return null;
  const line = `About ${mark} ("${source.title}"): `;
  return held.trim() ? `${held.trimEnd()}\n\n${line}` : line;
}

/** "Sep 29" — the list's date. */
export function shortDate(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  return at.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** "Tue 29 Sep" — the reader's date, in the board's order whatever the locale's punctuation. */
export function longDate(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  const part = (options: Intl.DateTimeFormatOptions) => at.toLocaleDateString(undefined, options);
  const year = at.getFullYear() === new Date().getFullYear() ? "" : ` ${at.getFullYear()}`;
  return `${part({ weekday: "short" })} ${part({ day: "numeric" })} ${part({ month: "short" })}${year}`;
}

/* ── Files ──────────────────────────────────────────────────────────────── */

export type SortBy = "name" | "size" | "modified";

/** Folders first, then the chosen column. Size and date descend — "biggest" and "most recent"
 *  are what those questions mean. */
export function sortEntries<T extends { name: string; type: string; size: number; modified?: number }>(
  entries: readonly T[],
  sortBy: SortBy,
): T[] {
  return [...entries].sort((a, b) => {
    if (a.type !== b.type) return a.type === "dir" ? -1 : 1;
    if (sortBy === "size") return b.size - a.size || a.name.localeCompare(b.name);
    if (sortBy === "modified") return (b.modified ?? 0) - (a.modified ?? 0) || a.name.localeCompare(b.name);
    return a.name.localeCompare(b.name);
  });
}

/** What the folder shows: dot-files only when asked for, and only names containing the filter. */
export function visibleEntries<T extends { name: string }>(
  entries: readonly T[],
  { showHidden, filter }: { showHidden: boolean; filter: string },
): T[] {
  const want = filter.trim().toLowerCase();
  return entries.filter(
    (entry) =>
      (showHidden || !entry.name.startsWith(".")) && (!want || entry.name.toLowerCase().includes(want)),
  );
}

/** Every folder above `path` and `path` itself, outermost first: `a/b/c` → `a`, `a/b`, `a/b/c`. */
export function ancestorsOf(path: string): string[] {
  if (!path || path === ".") return [];
  const parts = path.split("/").filter(Boolean);
  return parts.map((_, i) => parts.slice(0, i + 1).join("/"));
}

/** `a/b` + `c` → `a/b/c`, with the root spelled `.` as the listing API spells it. */
export function joinPath(dir: string, name: string): string {
  return dir === "." || !dir ? name : `${dir}/${name}`;
}

/** The folder above, or `.` at the top. */
export function parentOf(path: string): string {
  const parts = path.split("/").filter(Boolean);
  return parts.length > 1 ? parts.slice(0, -1).join("/") : ".";
}

/** "Today, 10:36 AM", "Yesterday, 6:32 PM", "Mon, 9:41 AM", then "12 Jun" — the file list's
 *  modified column, which wants the time while it is recent and only the date after a week. */
export function modifiedLabel(epochSeconds: number, now = new Date()): string {
  if (!epochSeconds) return "";
  const at = new Date(epochSeconds * 1000);
  const midnight = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((midnight(now) - midnight(at)) / 86_400_000);
  const clock = at.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  if (days <= 0) return `Today, ${clock}`;
  if (days === 1) return `Yesterday, ${clock}`;
  if (days < 7) return `${at.toLocaleDateString(undefined, { weekday: "short" })}, ${clock}`;
  if (at.getFullYear() === now.getFullYear())
    return at.toLocaleDateString(undefined, { day: "numeric", month: "short" });
  return at.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

/** Lines as an editor counts them: a trailing newline ends the last line, it does not start one. */
export function lineTotal(text: string): number {
  if (!text) return 0;
  const n = text.split("\n").length;
  return text.endsWith("\n") ? n - 1 : n;
}

/** An absolute path the way the screen prints it: the home folder as `~`. */
export function homeShort(path: string): string {
  return path.replace(/^\/Users\/[^/]+/, "~").replace(/^\/home\/[^/]+/, "~");
}
