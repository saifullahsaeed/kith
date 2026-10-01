/**
 * What the task page reads out of a task, kept apart from how it draws it.
 *
 * Most of the page is a reading of things he wrote — the plan file, the checklist, what he filed —
 * and every reading here is a guess about prose that nobody wrote for a parser. So they are pure
 * functions with tests beside them, rather than regexes inlined in JSX where a plan shaped slightly
 * differently would quietly render as nothing.
 */
import type { ChecklistItem, Deliverable, Task, TaskDetail } from "@/lib/backend/brain";

/* ── The plan ───────────────────────────────────────────────────────────── */

export type PlanBlock =
  | { kind: "paragraph"; raw: string }
  | { kind: "list"; raw: string; items: string[] }
  | { kind: "table"; raw: string; header: string[]; rows: string[][] }
  | { kind: "quote"; raw: string }
  | { kind: "code"; raw: string }
  | { kind: "heading"; raw: string; text: string };

export interface PlanSection {
  /** The `##` heading, as written. */
  title: string;
  blocks: PlanBlock[];
}

export interface Plan {
  /** What sits between the title and the first section — usually a line of filing metadata
   *  ("Milestone 69 · filed 17 Sep"), sometimes a real note. Never shown in the preview. */
  preamble: string;
  sections: PlanSection[];
}

const FENCE = /^\s*(```|~~~)/;
const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const LIST_ITEM = /^\s{0,3}(?:[-*+]|\d+[.)])\s+/;
const TABLE_ROW = /^\s*\|.*\|\s*$/;

/**
 * A plan file, split into its `##` sections and each section into blocks.
 *
 * His plans all open with a `# ` title that repeats the task's goal — the page already says that
 * in 22px, so it is dropped. A further `# ` (one plan has two, from being rewritten) is read as a
 * section like a `##`. `###` and deeper stay inside their section: they subdivide it, and a plan
 * cut at every level would turn "What changes › Backend" into a section called "Backend".
 */
export function parsePlan(md: string): Plan {
  const lines = md.replace(/\r\n?/g, "\n").split("\n");
  let preamble: string[] = [];
  const sections: { title: string; lines: string[] }[] = [];
  let sawTitle = false;
  let fenced = false;

  for (const line of lines) {
    if (FENCE.test(line)) fenced = !fenced;
    const heading = fenced ? null : HEADING.exec(line);
    if (heading && heading[1].length <= 2) {
      const level = heading[1].length;
      if (level === 1 && !sawTitle && !sections.length && !preamble.some((one) => one.trim())) {
        sawTitle = true;
        continue;
      }
      sections.push({ title: heading[2].trim(), lines: [] });
      continue;
    }
    if (sections.length) sections[sections.length - 1].lines.push(line);
    else preamble.push(line);
  }

  // Trailing blank lines from the split are not content.
  preamble = trimBlank(preamble);
  return {
    preamble: preamble.join("\n"),
    sections: sections.map((one) => ({ title: one.title, blocks: blocksOf(trimBlank(one.lines)) })),
  };
}

function trimBlank(lines: string[]): string[] {
  let start = 0;
  let end = lines.length;
  while (start < end && !lines[start].trim()) start++;
  while (end > start && !lines[end - 1].trim()) end--;
  return lines.slice(start, end);
}

/** Split a section's lines into the blocks Markdown would see. Close enough to CommonMark for
 *  his writing — which is paragraphs, dash lists, pipe tables, quotes and fences — and no closer. */
export function blocksOf(lines: string[]): PlanBlock[] {
  const out: PlanBlock[] = [];
  let at = 0;
  while (at < lines.length) {
    const line = lines[at];
    if (!line.trim()) {
      at++;
      continue;
    }
    if (FENCE.test(line)) {
      const start = at;
      at++;
      while (at < lines.length && !FENCE.test(lines[at])) at++;
      at++; // the closing fence
      out.push({ kind: "code", raw: lines.slice(start, at).join("\n") });
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      out.push({ kind: "heading", raw: line, text: heading[2].trim() });
      at++;
      continue;
    }
    if (TABLE_ROW.test(line)) {
      const start = at;
      while (at < lines.length && TABLE_ROW.test(lines[at])) at++;
      const rows = lines.slice(start, at).map(cells);
      const body = rows.slice(1).filter((row) => !row.every((cell) => /^:?-{2,}:?$/.test(cell.trim())));
      out.push({ kind: "table", raw: lines.slice(start, at).join("\n"), header: rows[0] ?? [], rows: body });
      continue;
    }
    if (/^\s*>/.test(line)) {
      const start = at;
      while (at < lines.length && /^\s*>/.test(lines[at])) at++;
      out.push({ kind: "quote", raw: lines.slice(start, at).join("\n") });
      continue;
    }
    if (LIST_ITEM.test(line)) {
      const start = at;
      const items: string[] = [];
      while (at < lines.length) {
        const here = lines[at];
        if (LIST_ITEM.test(here)) {
          items.push(here.replace(LIST_ITEM, "").trim());
        } else if (here.trim() && /^\s+/.test(here) && items.length) {
          // A continuation: wrapped text, or a nested item, which reads as part of its parent.
          items[items.length - 1] += ` ${here.trim().replace(LIST_ITEM, "")}`;
        } else if (!here.trim() && LIST_ITEM.test(lines[at + 1] ?? "")) {
          // A loose list — blank lines between items — is still one list.
        } else {
          break;
        }
        at++;
      }
      out.push({ kind: "list", raw: lines.slice(start, at).join("\n"), items });
      continue;
    }
    const start = at;
    while (
      at < lines.length &&
      lines[at].trim() &&
      !FENCE.test(lines[at]) &&
      !HEADING.test(lines[at]) &&
      !TABLE_ROW.test(lines[at]) &&
      !LIST_ITEM.test(lines[at]) &&
      !/^\s*>/.test(lines[at])
    ) {
      at++;
    }
    out.push({ kind: "paragraph", raw: lines.slice(start, at).join("\n") });
  }
  return out;
}

function cells(row: string): string[] {
  return row
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

/** Markdown down to the words: for measuring, summarising, and one-line labels. */
export function plain(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/[*_`#>]+/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

/* ── A long table, as one line ──────────────────────────────────────────── */

export interface TableMark {
  tone: "yes" | "no" | "part";
  count: number;
  label: string;
}

export interface TableSummary {
  /** Data rows, not counting the header. */
  rows: number;
  /** "facts" when the rows carry verdicts (✅/❌), "rows" otherwise. */
  noun: string;
  /** The heading's own aside — "(verified this turn, not recalled)" → "verified this turn". */
  note: string;
  marks: TableMark[];
  /** What the section concludes from the table, when he said it in bold after it. */
  conclusion: string;
}

/** A table this long is collapsed to its summary even when the whole plan is open. Three rows is
 *  a comparison you read at a glance; six is evidence you consult. */
export const LONG_TABLE = 4;

const MARKS: { tone: TableMark["tone"]; re: RegExp; fallback: string }[] = [
  { tone: "yes", re: /✅|✔️?|☑️?/u, fallback: "confirmed" },
  { tone: "no", re: /❌|✖️?|⛔/u, fallback: "missing" },
  { tone: "part", re: /⚠️?|🟡/u, fallback: "partial" },
];

/**
 * The one line a long evidence table folds into: how many rows, how many came out which way, and
 * what he concluded from it.
 *
 * Each row counts once, for the first verdict mark in it. The label is the word he used beside the
 * mark when every row agrees on it ("✅ built" four times → "4 built"), and a plain word when they
 * do not — "❌ does not exist" and "❌ never set" are both "missing", and printing either one as the
 * label for both would misquote the other.
 */
export function summariseTable(section: PlanSection, table: Extract<PlanBlock, { kind: "table" }>): TableSummary {
  const tally = new Map<TableMark["tone"], string[]>();
  for (const row of table.rows) {
    const joined = row.join(" | ");
    let first: { tone: TableMark["tone"]; index: number; re: RegExp } | null = null;
    for (const mark of MARKS) {
      const found = mark.re.exec(joined);
      if (found && (first === null || found.index < first.index)) first = { tone: mark.tone, index: found.index, re: mark.re };
    }
    if (!first) continue;
    const after = joined.slice(first.index).replace(first.re, "");
    const word = plain(after).split(/[\s|—–,.;:()]+/).find(Boolean)?.toLowerCase() ?? "";
    tally.set(first.tone, [...(tally.get(first.tone) ?? []), word]);
  }
  const marks: TableMark[] = MARKS.flatMap((mark) => {
    const words = tally.get(mark.tone);
    if (!words?.length) return [];
    const agreed = words.every((word) => word && word === words[0]) && /^[a-z][a-z-]{1,15}$/.test(words[0]);
    return [{ tone: mark.tone, count: words.length, label: agreed ? words[0] : mark.fallback }];
  });

  const aside = /\(([^)]+)\)\s*$/.exec(section.title)?.[1] ?? "";
  const note = aside.split(/[,;]/)[0]?.trim() ?? "";

  const index = section.blocks.indexOf(table);
  const after = section.blocks
    .slice(index + 1)
    .filter((block) => block.kind === "paragraph")
    .map((block) => block.raw)
    .join("\n");
  // The first bold run that reads as a sentence — three words or more. A bold `file.viewall` is
  // emphasis on a name, not a conclusion.
  const bold = [...after.matchAll(/\*\*([^*]{8,90}?)\*\*/g)]
    .map((found) => plain(found[1]))
    .find((text) => text.split(/\s+/).length >= 3) ?? "";
  const conclusion = bold.replace(/[.:]+$/, "");

  return { rows: table.rows.length, noun: marks.length ? "facts" : "rows", note, marks, conclusion };
}

/** A section's heading, short enough for a row of links: no aside, nothing after a dash. */
export function shortTitle(title: string, max = 34): string {
  const tidy = plain(title)
    .replace(/\([^)]*\)/g, "")
    .replace(/[⚠️✅❌]/gu, "")
    .split(/\s+[—–-]\s+|:\s/)[0]
    .replace(/\s+/g, " ")
    .trim();
  return tidy.length > max ? `${tidy.slice(0, max - 1).trimEnd()}…` : tidy;
}

/* ── The plan's preview ─────────────────────────────────────────────────── */

export type PreviewSection =
  | { kind: "prose"; index: number; title: string; lead: string; leadLines: number; items: string[]; more: number }
  | { kind: "table"; index: number; title: string; summary: TableSummary };

export interface PlanPreview {
  shown: PreviewSection[];
  /** Sections that did not fit, by index into `plan.sections`, for "Also in the plan". */
  rest: number[];
}

/** The preview card's room for sections: 284px card, less its 34px footer and 12px top pad. */
export const PREVIEW_BUDGET = 238;
const LINE = 20;
const CHARS_PER_LINE = 105;
const ITEMS_SHOWN = 5;

/**
 * As much of the plan as fits in the card, in its own order, each section at its most
 * telling: a section with a table is its one-line summary; one that is a list is its items, a line
 * each; prose is clamped to three lines. What does not fit is named in the footer rather than cut
 * off mid-sentence under the fold.
 *
 * Heights are estimated, not measured — this decides *which* sections to draw, and measuring would
 * mean drawing them all first. The estimate only has to be right about whether the next section
 * fits; the card clips anyway, so a near miss costs a half line, not a broken layout.
 */
export function previewPlan(plan: Plan, budget = PREVIEW_BUDGET): PlanPreview {
  const shown: PreviewSection[] = [];
  const rest: number[] = [];
  let used = 0;
  plan.sections.forEach((section, index) => {
    const one = previewOf(section, index);
    const height = estimate(one, shown.length === 0);
    if (rest.length === 0 && (shown.length === 0 || used + height <= budget)) {
      shown.push(one);
      used += height;
      return;
    }
    // The first section that does not fit whole is drawn as far as it goes — fewer items, a
    // shorter lead — rather than leaving the card's bottom half empty under the one before it.
    const part = rest.length === 0 ? trimmed(one, budget - used) : null;
    if (part) {
      shown.push(part);
      used = budget;
      rest.push(-1); // a marker: nothing after this is drawn
      return;
    }
    rest.push(index);
  });
  return { shown, rest: rest.filter((index) => index >= 0) };
}

/** A prose section cut down to `room` px, or null when not even its heading and a line fit. */
function trimmed(one: PreviewSection, room: number): PreviewSection | null {
  if (one.kind !== "prose") return null;
  const avail = room - 12 - 18;
  if (avail < 4 + LINE) return null;
  const total = one.items.length + one.more;
  let leadLines = one.lead ? one.leadLines : 0;
  const leadHeight = () => (leadLines ? 4 + leadLines * LINE : 0);
  if (total) {
    // Items take a line each, plus one for "+N more".
    let fits = Math.floor((avail - leadHeight() - 4) / LINE) - 1;
    if (fits < 1 && leadLines > 1) {
      leadLines = 1;
      fits = Math.floor((avail - leadHeight() - 4) / LINE) - 1;
    }
    if (fits >= 1) {
      const kept = Math.min(fits, one.items.length);
      return { ...one, leadLines, items: one.items.slice(0, kept), more: total - kept };
    }
    if (!one.lead) return null;
    return { ...one, leadLines: Math.max(1, Math.min(leadLines, Math.floor((avail - 4) / LINE))), items: [], more: 0 };
  }
  leadLines = Math.min(leadLines, Math.floor((avail - 4) / LINE));
  return leadLines >= 1 ? { ...one, leadLines } : null;
}

function previewOf(section: PlanSection, index: number): PreviewSection {
  const table = section.blocks.find((block) => block.kind === "table");
  if (table && table.kind === "table") return { kind: "table", index, title: section.title, summary: summariseTable(section, table) };
  const firstList = section.blocks.findIndex((block) => block.kind === "list");
  const before = firstList < 0 ? section.blocks : section.blocks.slice(0, firstList);
  const lead = before
    .flatMap((block, at) => {
      if (block.kind !== "paragraph") return [];
      const flat = block.raw.replace(/\s*\n\s*/g, " ");
      const next = before[at + 1];
      return [next && (next.kind === "quote" || next.kind === "code") ? withoutLeadIn(flat) : flat];
    })
    .join(" ");
  const list = firstList >= 0 ? section.blocks[firstList] : null;
  const items = list && list.kind === "list" ? list.items : [];
  const leadLines = lead ? Math.min(items.length ? 2 : 3, Math.max(1, Math.ceil(plain(lead).length / CHARS_PER_LINE))) : 0;
  return {
    kind: "prose",
    index,
    title: section.title,
    lead,
    leadLines,
    items: items.slice(0, ITEMS_SHOWN),
    more: Math.max(0, items.length - ITEMS_SHOWN),
  };
}

/**
 * A paragraph that introduces a quote or a code block the preview leaves out, without the
 * introduction: "…always the primary membership — `AppRail.tsx:155` says so in its own comment:"
 * would otherwise run straight into the next paragraph, pointing at a quote that is not there.
 * Cut at the last dash, else at the last sentence; left whole when neither leaves anything.
 */
export function withoutLeadIn(paragraph: string): string {
  const text = paragraph.trim();
  if (!text.endsWith(":")) return text;
  const dash = text.lastIndexOf(" — ");
  if (dash > 8) return `${text.slice(0, dash).trimEnd()}.`;
  const stop = Math.max(text.lastIndexOf(". "), text.lastIndexOf("? "), text.lastIndexOf("! "));
  if (stop > 8) return text.slice(0, stop + 1);
  return text;
}

function estimate(one: PreviewSection, first: boolean): number {
  if (one.kind === "table") return 10 + 28;
  const top = first ? 0 : 12;
  const lead = one.lead ? 4 + one.leadLines * LINE : 0;
  const items = one.items.length ? 4 + (one.items.length + (one.more ? 1 : 0)) * LINE : 0;
  return top + 18 + lead + items;
}

/* ── Other tasks the plan names ─────────────────────────────────────────── */

/**
 * The `#id`s a plan mentions, other than its own, with the section each first appears in.
 *
 * Only the sections are read: the preamble is where "project #16" lives, and a project's number
 * is not a task's. A `#` after "project", "milestone" or "PR" is skipped for the same reason
 * wherever it is.
 */
export function namedTasks(plan: Plan, self: number): { id: number; section: string }[] {
  const out = new Map<number, string>();
  for (const section of plan.sections) {
    const text = `${section.title}\n${section.blocks.map((block) => block.raw).join("\n")}`;
    for (const found of text.matchAll(/(^|[^\w&/#])#(\d{1,5})\b/g)) {
      const id = Number(found[2]);
      const before = text.slice(Math.max(0, (found.index ?? 0) - 12), (found.index ?? 0) + found[1].length);
      if (id === self || out.has(id)) continue;
      if (/\b(project|milestone|pr|issue|pull)\s*$/i.test(before)) continue;
      out.set(id, section.title);
    }
  }
  return [...out].map(([id, section]) => ({ id, section }));
}

/* ── The checklist ──────────────────────────────────────────────────────── */

/** How many steps show before "N more". */
export const STEPS_SHOWN = 4;

/**
 * Which run of steps to show when the list is folded: the window around where he is.
 *
 * The first `size` steps of a task six-sevenths done are six ticks of history and nothing about
 * the one left; so the window starts one step before the first undone one — the last thing he
 * finished, then what is next. A list one longer than the window is shown whole: "1 more" in
 * place of the one line it hides saves nothing.
 */
export function stepWindow(items: Pick<ChecklistItem, "done">[], size = STEPS_SHOWN): { start: number; end: number } {
  if (items.length <= size + 1) return { start: 0, end: items.length };
  const next = items.findIndex((item) => !item.done);
  const start = next <= 0 ? 0 : Math.min(next - 1, items.length - size);
  return { start, end: start + size };
}

/** The folded steps, as one clause each: "clear the cache on switch, pin it, run the suite". */
export function stepsGist(texts: string[]): string {
  return texts
    .map((text) => {
      const flat = plain(text);
      const clause = flat.split(/\s+[—–]\s+|:\s|;\s|\.\s|\s\(|,\s+then\s+/)[0]?.trim() ?? flat;
      // Lower-case the opening word unless it is a name: "Read" → "read", "TopBar.tsx" stays.
      return /^[A-Z][a-z]+\b/.test(clause) && !/^[A-Z][a-z]+[A-Z.]/.test(clause)
        ? clause[0].toLowerCase() + clause.slice(1)
        : clause;
    })
    .filter(Boolean)
    .join(", ");
}

/* ── Activity ───────────────────────────────────────────────────────────── */

export interface ActivityEvent {
  /** Null when the record does not say who — a step added later has no author on it. */
  who: "you" | "kith" | null;
  text: string;
  at: string;
}

const NUMBER = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"];
export const count = (n: number, noun: string) =>
  `${NUMBER[n] ?? n} ${noun}${n === 1 ? "" : "s"}`;

const ms = (iso: string | undefined) => {
  const value = iso ? new Date(iso).getTime() : Number.NaN;
  return Number.isNaN(value) ? null : value;
};

/** Things filed within this of each other are one event: he attaches a set of designs in a
 *  minute, not four separate times. */
const SAME_MOVE = 30 * 60_000;
/** Within this of the task being filed counts as filed with it. */
const WITH_IT = 2 * 60_000;

export type ActivityInput = Pick<
  TaskDetail,
  "created_at" | "updated_at" | "created_by" | "status" | "plan" | "checklist" | "deliverables"
> & { plan_modified?: string };

/**
 * What happened to the task, from the timestamps it carries.
 *
 * There is no history table to read — the comment thread that used to be one was removed on
 * purpose — so this is what the rows themselves can prove: when it was filed and with what, when
 * steps were added after, when he attached things, and the status it is in now. A status change
 * has no timestamp of its own; `updated_at` stands in for it, and only for the moves after which
 * a task is rarely touched again (approved, done, dropped) or with words that claim no more than
 * "last moved" for one he is still working.
 */
export function taskActivity(task: ActivityInput, isDesign: (d: Deliverable) => boolean = () => false): ActivityEvent[] {
  const filed = ms(task.created_at) ?? 0;
  const events: ActivityEvent[] = [];
  const planAt = ms(task.plan_modified);
  const planWithIt = Boolean(task.plan) && (planAt === null ? task.created_by !== "user" : planAt - filed <= WITH_IT);

  const withIt = task.checklist.filter((item) => (ms(item.created_at) ?? 0) - filed <= WITH_IT);
  const later = task.checklist.filter((item) => (ms(item.created_at) ?? 0) - filed > WITH_IT);

  const filing =
    task.created_by === "user"
      ? "Added this task"
      : `Filed this${planWithIt ? " with its plan" : ""}${
          withIt.length ? `${planWithIt ? " and" : " with"} ${count(withIt.length, "step")}` : ""
        }`;
  events.push({ who: task.created_by === "user" ? "you" : "kith", text: filing, at: task.created_at });

  if (task.plan && planAt !== null && !planWithIt) {
    events.push({ who: "kith", text: "Wrote the plan", at: task.plan_modified as string });
  }

  for (const group of clusters(later, (item) => item.created_at)) {
    events.push({ who: null, text: `Added ${count(group.length, "step")}`, at: group[0].created_at });
  }

  for (const group of clusters(task.deliverables, (d) => d.created_at)) {
    const noun = group.every(isDesign)
      ? "design"
      : group.every((d) => d.kind === "link")
        ? "link"
        : group.every((d) => d.kind === "file")
          ? "file"
          : "deliverable";
    const text =
      group.length === 1
        ? `Attached ${group[0].title || "a " + noun}`
        : `Attached ${count(group.length, noun)}: ${group.map((d) => d.title).filter(Boolean).join(", ")}`;
    events.push({ who: "kith", text, at: group[0].created_at });
  }

  const touched = ms(task.updated_at);
  const latest = Math.max(filed, ...events.map((event) => ms(event.at) ?? 0));
  if (touched !== null && touched - latest > WITH_IT) {
    const done = task.checklist.filter((item) => item.done).length;
    const moved: Record<string, ActivityEvent | null> = {
      approved: { who: "you", text: "Approved the plan — it's up next", at: task.updated_at },
      working: {
        who: "kith",
        text: task.checklist.length ? `Last moved it on — ${done} of ${task.checklist.length} steps done` : "Last moved it on",
        at: task.updated_at,
      },
      done: { who: "kith", text: "Marked it done", at: task.updated_at },
      dropped: { who: null, text: "Dropped it", at: task.updated_at },
    };
    const event = moved[task.status];
    if (event) events.push(event);
  } else if (touched !== null && task.status === "done") {
    // Finished in the same breath as the last thing he attached — still worth its own line.
    events.push({ who: "kith", text: "Marked it done", at: task.updated_at });
  }

  return events
    .map((event, order) => ({ event, order }))
    .sort((a, b) => (ms(a.event.at) ?? 0) - (ms(b.event.at) ?? 0) || a.order - b.order)
    .map(({ event }) => event);
}

function clusters<T>(items: T[], at: (item: T) => string): T[][] {
  const sorted = [...items].sort((a, b) => (ms(at(a)) ?? 0) - (ms(at(b)) ?? 0));
  const out: T[][] = [];
  for (const item of sorted) {
    const last = out[out.length - 1];
    if (last && (ms(at(item)) ?? 0) - (ms(at(last[last.length - 1])) ?? 0) <= SAME_MOVE) last.push(item);
    else out.push([item]);
  }
  return out;
}

/* ── Moving through the waiting plans ───────────────────────────────────── */

const PRIORITY_RANK: Record<string, number> = { high: 0, normal: 1, low: 2 };

/** A project's plans waiting for your OK, in the order its board's Planning column shows them:
 *  priority, then id — stable, so J and K walk the same list you see there. */
export function waitingPlans(tasks: Task[], projectId: number | null): Task[] {
  return tasks
    .filter((task) => task.status === "planning" && (task.project_id ?? null) === projectId)
    .sort((a, b) => (PRIORITY_RANK[a.priority] ?? 1) - (PRIORITY_RANK[b.priority] ?? 1) || a.id - b.id);
}

/** Where J and K go from here. Off the list (a task that is not waiting), J starts at its top
 *  and K at its bottom; on it, they stop at the ends rather than wrapping round. */
export function stepThrough(list: Pick<Task, "id">[], id: number): { prev: number | null; next: number | null } {
  const at = list.findIndex((task) => task.id === id);
  if (at < 0) return { prev: list[list.length - 1]?.id ?? null, next: list[0]?.id ?? null };
  return { prev: list[at - 1]?.id ?? null, next: list[at + 1]?.id ?? null };
}

/* ── Small readings ─────────────────────────────────────────────────────── */

/** "4.0 KB", "12.5 KB" — one decimal, which is what tells four near-identical designs apart. */
export function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** "13 days", "5 hours", "20 min" — how long something has been waiting. */
export function waited(fromIso: string, now = Date.now()): string {
  const from = ms(fromIso);
  if (from === null) return "";
  const minutes = Math.max(0, Math.round((now - from) / 60_000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"}`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"}`;
}

/** The text a "Request changes" or a reply carries into the chat it starts, so the chat knows
 *  which task — and which plan — it is about. */
export function chatAbout(task: Pick<Task, "id" | "goal">, note: string, changes: boolean): string {
  const said = note.trim();
  return changes
    ? `Before I approve #${task.id} (“${task.goal}”), change the plan:\n\n${said}`
    : `About #${task.id} (“${task.goal}”):\n\n${said}`;
}
