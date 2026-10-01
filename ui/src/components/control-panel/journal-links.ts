/** What a journal entry is connected to, read out of the entry itself.
 *
 * Nothing in the database links an entry to a project or to the memory he saved off the back of
 * it — a journal entry is one column of text. But the links are there to be read: he names the
 * project he is writing about ("the Ironline sign-in"), he names the files he touched, and when
 * an entry ends "wrote it into memory" the memory row lands a few seconds after the journal row.
 * These are pure so the guesses can be tested against his real sentences. */

/** The part of a project's name he actually says: "Ironline Haulage — operations portal" is
 *  "Ironline Haulage" in a sentence, "Asterion (working codename)" is "Asterion". */
export function spokenName(name: string): string {
  return name.split(/\s+[—–-]\s+|\s*\(|:\s/)[0]!.trim();
}

const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

type Named = { id: number; name: string; status?: string; updated_at?: string; directory?: string | null };

/** Words that open a project's name without being its name. */
const GENERIC = new Set(["Project", "Projects", "Test", "New", "Mission"]);

/**
 * The projects an entry mentions, in the order it mentions them.
 *
 * Matched on the full name and on its spoken head, case-sensitively and on word boundaries.
 * Case matters because a project called "placeholder" must not claim every entry that uses the
 * word, so a name with no capital letter in it is never matched by name at all. "project #35"
 * matches by id. So does the first word alone — "the Ironline sign-in" — when it is the project's
 * own coined word: only one project starts with it, and its folder does too (`ironline-haulage`).
 * That second test is what keeps "Microsoft" (a vendor he mentions, and the first word of one
 * project) from tagging every entry about SSO.
 *
 * A match inside a longer one is dropped: an entry about "Sadeef AI connector runtime" is about
 * that project, not also about "Sadeef AI". Two rows with the same name (the board has a
 * duplicate or two) are one mention — the active one, else the newest.
 */
export function mentionedProjects<P extends Named>(text: string, projects: readonly P[]): P[] {
  return projectMatcher(projects)(text);
}

/** `mentionedProjects` with the patterns compiled once, for running over many texts. */
export function projectMatcher<P extends Named>(projects: readonly P[]): (text: string) => P[] {
  const patterns: { project: P; pattern: RegExp }[] = [];
  const firstWord = (project: P) => spokenName(project.name).split(/\s+/)[0] ?? "";
  const openers = new Map<string, number>();
  for (const project of projects) openers.set(firstWord(project), (openers.get(firstWord(project)) ?? 0) + 1);
  for (const project of projects) {
    const names = new Set([project.name.trim(), spokenName(project.name)]);
    const first = firstWord(project);
    const folder = (project.directory ?? "").split("/").filter(Boolean).pop()?.toLowerCase() ?? "";
    if (
      first !== spokenName(project.name) &&
      first.length >= 5 &&
      openers.get(first) === 1 &&
      !GENERIC.has(first) &&
      folder.startsWith(first.toLowerCase())
    ) {
      names.add(first);
    }
    for (const name of names) {
      if (name.length < 4 || !/[A-Z]/.test(name)) continue;
      patterns.push({ project, pattern: new RegExp(`(?<![\\w])${escape(name)}(?![\\w])`, "g") });
    }
  }
  const byId = new Map(projects.map((p) => [p.id, p]));
  const rank = (p: P) => (p.status === "active" ? 1 : 0);

  return (text) => {
    type Hit = { project: P; start: number; end: number };
    const hits: Hit[] = [];
    for (const { project, pattern } of patterns) {
      for (const match of text.matchAll(pattern)) {
        hits.push({ project, start: match.index, end: match.index + match[0].length });
      }
    }
    for (const match of text.matchAll(/\bproject\s+#?(\d+)\b/gi)) {
      const project = byId.get(Number(match[1]));
      if (project) hits.push({ project, start: match.index, end: match.index + match[0].length });
    }
    const kept = hits.filter(
      (hit) =>
        !hits.some(
          (other) =>
            other !== hit &&
            other.start <= hit.start &&
            other.end >= hit.end &&
            other.end - other.start > hit.end - hit.start,
        ),
    );
    kept.sort((a, b) => a.start - b.start);

    const chosen = new Map<string, P>();
    for (const { project } of kept) {
      const seen = chosen.get(project.name);
      if (
        !seen ||
        rank(project) > rank(seen) ||
        (rank(project) === rank(seen) && (project.updated_at ?? "") > (seen.updated_at ?? ""))
      ) {
        chosen.set(project.name, project);
      }
    }
    return [...chosen.values()];
  };
}

const FILE = /(?:~\/|\/)?(?:[\w.-]+\/)*[\w.-]*[\w-]\.(?:py|tsx?|jsx?|mjs|cjs|md|json|html?|css|scss|sh|ya?ml|toml|sql|txt|csv|swift|rs|go|java|kt|rb|php|ipynb)\b/g;
const PATH = /~\/[\w.\-/]+/g;

/** The files and folders an entry names — `check_design.py`, `~/Kith/pr-review-action` — at most
 *  `limit`, first mention first. */
export function mentionedFiles(text: string, limit = 6): string[] {
  const found: { at: number; name: string }[] = [];
  for (const match of text.matchAll(PATH)) found.push({ at: match.index, name: match[0] });
  for (const match of text.matchAll(FILE)) found.push({ at: match.index, name: match[0] });
  found.sort((a, b) => a.at - b.at);
  const out: string[] = [];
  for (const { name } of found) {
    const tidy = name.replace(/[.,;:/]+$/, "");
    // "Node.js" is a runtime, not a file he touched.
    if (/^[A-Z][a-z]+\.js$/.test(tidy)) continue;
    // A file inside a path already listed is the same mention, said twice.
    if (tidy.length < 3 || out.some((seen) => seen === tidy || seen.includes(tidy))) continue;
    out.push(tidy);
    if (out.length >= limit) break;
  }
  return out;
}

/** How far apart an entry and a memory can be saved and still be one thought. He writes the
 *  entry and the memory in one turn; on his real rows the gap is nine seconds. */
export const KEPT_WINDOW = 60_000;

/** The memories saved within a minute of an entry, nearest first, each with its offset in ms
 *  (positive: saved after the entry). */
export function keptMemories<M extends { created_at: string }>(
  entryAt: number,
  memories: readonly M[],
  window = KEPT_WINDOW,
): { memory: M; offset: number }[] {
  const out: { memory: M; offset: number }[] = [];
  for (const memory of memories) {
    const at = new Date(memory.created_at).getTime();
    if (Number.isNaN(at)) continue;
    const offset = at - entryAt;
    if (Math.abs(offset) <= window) out.push({ memory, offset });
  }
  return out.sort((a, b) => Math.abs(a.offset) - Math.abs(b.offset));
}

/** "Saved 9 seconds after this entry". */
export function keptLabel(offset: number): string {
  const seconds = Math.round(Math.abs(offset) / 1000);
  if (seconds === 0) return "Saved with this entry";
  return `Saved ${seconds} second${seconds === 1 ? "" : "s"} ${offset > 0 ? "after" : "before"} this entry`;
}

/** Words in his markdown: whitespace-separated, not counting a token that is only markup — a
 *  heading's `#`, a list's `-`, a table's `|`, a code fence. */
export function wordCount(text: string): number {
  return text.split(/\s+/).filter((token) => token && !/^(?:[#>*|-]+|`{3}\w*)$/.test(token)).length;
}

/** The first line of an entry as plain text — what the day list shows under its date. */
export function firstLine(text: string): string {
  const line =
    text
      .split("\n")
      .map((l) => l.trim())
      .find((l) => /\w/.test(l)) ?? "";
  return line
    .replace(/^#+\s*/, "")
    .replace(/[*_`]+/g, "")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1");
}

/** Markdown down to one line of plain text. */
export function plain(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/[*_`#>]+/g, "")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
}

/** The first sentence of some markdown (cut at a word near `max` when it runs on), and the rest. */
export function sentence(text: string, max = 110): [string, string] {
  const flat = plain(text);
  const end = flat.search(/[.!?](\s|$)/);
  if (end > 12 && end < max) return [flat.slice(0, end + 1), flat.slice(end + 1).trim()];
  if (flat.length <= max) return [flat, ""];
  const cut = flat.slice(0, max);
  const space = cut.lastIndexOf(" ");
  const head = (space > max * 0.6 ? cut.slice(0, space) : cut).trimEnd();
  return [`${head}…`, flat.slice(head.length).trim()];
}
