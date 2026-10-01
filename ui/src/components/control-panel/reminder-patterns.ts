/**
 * What his reminders have in common — the Reminders screen's "They keep repeating" card, its Past
 * list folded by day, and the Schedules screen's suggestions all come from here.
 *
 * Why it exists: his real reminders are 24 rows of which 23 are him coming back to a test or CI
 * run he had started, each set a few minutes ahead. Listed one per row they are a wall; grouped,
 * they say "you keep asking for the same three things", which is the case for a standing job.
 *
 * The grouping is deliberately plain — a few named kinds matched on the note's *first clause*
 * (what it is mostly about), and for anything else the note's leading words — because a
 * suggestion he cannot trace back to the reminders behind it is not one anybody would accept.
 */
import { clock, clockText, shortDate, timeSpan, weekday } from "./reminder-time";

/** The fields of a reminder this module reads. */
export interface ReminderLike {
  id: number;
  note: string;
  status: string;
  fire_at: string;
  created_at: string;
  conversation_id?: string | null;
}

export interface ScheduleLike {
  note: string;
}

/* ── Kinds ──────────────────────────────────────────────────────────────── */

export interface Kind {
  key: string;
  /** How the kind reads as a line of its own: "Check a GitHub Actions run". */
  label: string;
  /** The same, counted: (4) → "4 GitHub Actions checks". */
  count: (n: number) => string;
  /** What he was coming back to, for "were him coming back to …". Null when it is not a check. */
  about: string | null;
}

const counted = (one: string, many: string) => (n: number) => `${n} ${n === 1 ? one : many}`;

/**
 * The named kinds, in priority order. Matched against the first clause, so "Check the pytest run.
 * If it passes, … monitor GitHub Actions" is a test check, while "Check Task #99 full local pytest
 * run and GitHub Actions run …" — CI named in the clause that says what to do — is a CI check.
 */
const NAMED: readonly (Kind & { test: (clause: string) => boolean })[] = [
  {
    key: "alarm",
    label: "An alarm",
    count: counted("alarm", "alarms"),
    about: null,
    test: (c) => /\balarm\b|\bwake (me|him) up\b/.test(c),
  },
  {
    key: "gates",
    label: "Collect backend and frontend test gates",
    count: counted("test-gate collection", "test-gate collections"),
    about: "a test run",
    test: (c) => /^collect\b/.test(c) || /\b(test )?gates?\b/.test(c),
  },
  {
    key: "ci",
    label: "Check a GitHub Actions run",
    count: counted("GitHub Actions check", "GitHub Actions checks"),
    about: "a CI run",
    test: (c) => /github actions|\bci\b|deploy runs?\b|workflow run/.test(c),
  },
  {
    key: "tests",
    label: "Re-check a running pytest suite",
    count: counted("pytest re-check", "pytest re-checks"),
    about: "a test run",
    test: (c) => /\bpytest\b|\btest (suite|run)\b|\bfull (local )?suite\b/.test(c),
  },
];

/** Words that say nothing about what a reminder is for. */
const FILLER = new Set(["the", "a", "an", "to", "and", "on", "of", "for", "my", "his", "please", "re", "again"]);

/** The note's first clause, lower-cased: up to the first full stop or semicolon. */
function firstClause(note: string): string {
  return note.toLowerCase().split(/[.;](?:\s|$)/, 1)[0] ?? "";
}

/** Which kind a note is. Anything unnamed is keyed by its first three meaningful words. */
export function kindOf(note: string): Kind {
  const clause = firstClause(note);
  const whole = note.toLowerCase();
  const named = NAMED.find((kind) => kind.test(clause)) ?? NAMED.find((kind) => kind.test(whole));
  if (named) return named;

  const words = clause
    .replace(/#\d+|\b\d[\w:]*\b/g, " ")
    .split(/[^a-z']+/)
    .filter((word) => word && !FILLER.has(word))
    .slice(0, 3);
  const lead = words.join(" ");
  const label = leadingWords(note, 6);
  return {
    key: `lead:${lead || note.trim().toLowerCase()}`,
    label,
    count: (n) => `${n} × “${leadingWords(note, 4)}”`,
    about: null,
  };
}

/** The first `n` words of a note, with an ellipsis when it goes on. */
function leadingWords(note: string, n: number): string {
  const words = note.trim().split(/\s+/);
  return words.length > n ? `${words.slice(0, n).join(" ")}…` : words.join(" ");
}

export interface Pattern {
  kind: Kind;
  /** Newest first. */
  items: ReminderLike[];
}

const ms = (iso: string) => {
  const t = new Date(iso).getTime();
  return Number.isNaN(t) ? 0 : t;
};

/** Reminders grouped by kind, biggest group first; ties go to the more recent. */
export function patterns(reminders: readonly ReminderLike[]): Pattern[] {
  const groups = new Map<string, Pattern>();
  for (const reminder of [...reminders].sort((a, b) => ms(b.fire_at) - ms(a.fire_at))) {
    const kind = kindOf(reminder.note);
    const group = groups.get(kind.key);
    if (group) group.items.push(reminder);
    else groups.set(kind.key, { kind, items: [reminder] });
  }
  return [...groups.values()].sort(
    (a, b) => b.items.length - a.items.length || ms(b.items[0].fire_at) - ms(a.items[0].fire_at),
  );
}

/* ── "They keep repeating" ──────────────────────────────────────────────── */

export interface RepeatSummary {
  /** Reminders in a kind that happened more than once. */
  repeated: number;
  total: number;
  /** What follows the "23 of 24": "were him coming back to a test or CI run he had started — …". */
  sentence: string;
  kinds: { key: string; label: string; n: number }[];
}

/** Null when nothing repeats — then there is no card to show. */
export function repeatSummary(reminders: readonly ReminderLike[]): RepeatSummary | null {
  const groups = patterns(reminders);
  const repeats = groups.filter((group) => group.items.length > 1);
  if (repeats.length === 0) return null;
  const repeated = repeats.reduce((sum, group) => sum + group.items.length, 0);

  const abouts = [...new Set(repeats.map((group) => group.kind.about))];
  const what = abouts.includes(null)
    ? `were the same ${repeats.length === 1 ? "nudge" : `${repeats.length} kinds of nudge`}, set again and again`
    : `were him coming back to ${orList(abouts as string[])} he had started`;

  const leads = repeats
    .flatMap((group) => group.items)
    .map((r) => Math.round((ms(r.fire_at) - ms(r.created_at)) / 60_000))
    .filter((minutes) => minutes > 0);
  const lo = Math.min(...leads);
  const hi = Math.max(...leads);
  const ahead =
    leads.length === 0
      ? ""
      : lo === hi
        ? ` — each one set ${aheadPhrase(lo)} ahead`
        : ` — each one set ${aheadRange(lo, hi)} ahead`;

  return {
    repeated,
    total: reminders.length,
    sentence: `${what}${ahead}.`,
    kinds: groups.slice(0, 5).map((group) => ({ key: group.kind.key, label: group.kind.label, n: group.items.length })),
  };
}

/** "a test run or a CI run" → "a test or CI run" when the nouns share their last word. */
function orList(items: string[]): string {
  if (items.length === 1) return items[0];
  const tails = items.map((item) => item.split(" ").slice(-1)[0]);
  if (tails.every((tail) => tail === tails[0])) {
    // Only the first keeps its article: "a test or CI run", not "a test or a CI run".
    const heads = items.map((item, i) => {
      const head = item.split(" ").slice(0, -1).join(" ");
      return i ? head.replace(/^an? /, "") : head;
    });
    return `${heads.slice(0, -1).join(", ")} or ${heads[heads.length - 1]} ${tails[0]}`;
  }
  return `${items.slice(0, -1).join(", ")} or ${items[items.length - 1]}`;
}

function aheadPhrase(minutes: number): string {
  if (minutes < 120) return `${minutes} minute${minutes === 1 ? "" : "s"}`;
  const hours = Math.round(minutes / 60);
  return `${hours} hours`;
}

function aheadRange(lo: number, hi: number): string {
  if (hi < 120) return `${lo} to ${hi} minutes`;
  return `${aheadPhrase(lo)} to ${aheadPhrase(hi)}`;
}

/* ── Past, folded by day ────────────────────────────────────────────────── */

export interface DayFold {
  /** Local `YYYY-MM-DD`. */
  key: string;
  date: string;
  dow: string;
  /** Newest first. */
  items: ReminderLike[];
  /** "4 reminders · all fired". */
  summary: string;
  /** What the day's reminders were about. */
  preview: string;
  /** "8:47 – 10:35 PM". */
  span: string;
}

/** How a status reads in Past. "done" is the scheduler's word for having fired. */
export function statusWord(status: string): string {
  if (status === "done") return "fired";
  if (status === "cancelled") return "cancelled";
  return status;
}

/** Past reminders by the local day they were due, newest day first. */
export function foldByDay(past: readonly ReminderLike[]): DayFold[] {
  const days = new Map<string, ReminderLike[]>();
  for (const reminder of [...past].sort((a, b) => ms(b.fire_at) - ms(a.fire_at))) {
    const at = new Date(reminder.fire_at);
    const key = `${at.getFullYear()}-${String(at.getMonth() + 1).padStart(2, "0")}-${String(at.getDate()).padStart(2, "0")}`;
    const list = days.get(key);
    if (list) list.push(reminder);
    else days.set(key, [reminder]);
  }
  return [...days.entries()].map(([key, items]) => {
    const newest = new Date(items[0].fire_at);
    const oldest = new Date(items[items.length - 1].fire_at);
    return {
      key,
      date: shortDate(newest),
      dow: weekday(newest),
      items,
      summary: daySummary(items),
      preview: dayPreview(items),
      span: timeSpan(oldest, newest),
    };
  });
}

/** "1 reminder · fired", "4 reminders · all fired", "3 reminders · 2 fired, 1 cancelled". */
export function daySummary(items: readonly ReminderLike[]): string {
  const n = items.length;
  const by = new Map<string, number>();
  for (const item of items) by.set(statusWord(item.status), (by.get(statusWord(item.status)) ?? 0) + 1);
  const head = `${n} reminder${n === 1 ? "" : "s"}`;
  if (by.size === 1) {
    const [word] = by.keys();
    return n === 1 ? `${head} · ${word}` : `${head} · all ${word}`;
  }
  return `${head} · ${[...by.entries()].map(([word, count]) => `${count} ${word}`).join(", ")}`;
}

/**
 * What a day's reminders were about, on one line: the note itself when there is one, otherwise
 * the kinds counted ("8 pytest re-checks, 2 GitHub Actions checks") and the tasks they name.
 */
export function dayPreview(items: readonly ReminderLike[]): string {
  if (items.length === 1) return items[0].note.replace(/\s+/g, " ").trim();
  const groups = patterns(items);
  const kinds = groups.map((group) => group.kind.count(group.items.length)).join(", ");
  const refs = taskRefs(items.map((item) => item.note));
  return refs ? `${kinds} · ${refs}` : kinds;
}

/** The tasks a set of notes name: "Task #74", "Tasks #88 and #90", "Tasks #74, #79 and #80". */
export function taskRefs(notes: readonly string[], max = 3): string {
  const seen: string[] = [];
  for (const note of notes) {
    for (const match of note.matchAll(/#(\d+)\b/g)) if (!seen.includes(match[1])) seen.push(match[1]);
  }
  if (seen.length === 0) return "";
  const shown = seen.slice(0, max).map((id) => `#${id}`);
  if (shown.length === 1) return `Task ${shown[0]}`;
  const more = seen.length > max ? ` and ${seen.length - max} more` : "";
  return more
    ? `Tasks ${shown.join(", ")}${more}`
    : `Tasks ${shown.slice(0, -1).join(", ")} and ${shown[shown.length - 1]}`;
}

/* ── Suggested schedules ────────────────────────────────────────────────── */

export interface Suggestion {
  key: string;
  /** `HH:MM`, local — what `daily_at` is sent as. */
  dailyAt: string;
  /** What the standing job would tell him to do. */
  note: string;
  /** One real reminder behind it, quoted. */
  quote: string;
  /** "Based on 10 reminders · Aug 6 and Aug 12". */
  basis: string;
  count: number;
  /** The chat the most recent of them came from, so the job can post where they did. */
  conversationId: string | null;
}

/**
 * Standing jobs his reminders argue for: every kind that repeated, plus any alarm (an alarm is a
 * daily thing even when it was only set once). Kinds already covered by a schedule are left out,
 * so adding a suggestion is also what retires it.
 *
 * `projectFor` names the project a chat belongs to, when it does — "Run the full Sadeef AI test
 * suite" rather than "the test suite", because that is which suite the reminders were about.
 */
export function suggestSchedules(
  reminders: readonly ReminderLike[],
  schedules: readonly ScheduleLike[],
  projectFor: (conversationId: string) => string | undefined = () => undefined,
): Suggestion[] {
  const covered = new Set(schedules.map((schedule) => kindOf(schedule.note).key));
  return patterns(reminders)
    .filter((group) => group.items.length > 1 || group.kind.key === "alarm")
    .filter((group) => !covered.has(group.kind.key))
    .map((group) => {
      const { items, kind } = group;
      const conversationId = items.find((item) => item.conversation_id)?.conversation_id ?? null;
      const project = mostCommon(
        items.map((item) => (item.conversation_id ? projectFor(item.conversation_id) : undefined)),
      );
      const quote = [...items].sort((a, b) => a.note.length - b.note.length)[0].note;
      return {
        key: kind.key,
        dailyAt: kind.key === "alarm" ? localClock(items[0].fire_at) : typicalClock(items.map((item) => item.fire_at)),
        note: jobFor(kind, items, project),
        quote,
        basis: basisFor(items),
        count: items.length,
        conversationId,
      };
    });
}

/** The job a kind of reminder becomes. Unnamed kinds keep his own words. */
function jobFor(kind: Kind, items: readonly ReminderLike[], project: string | undefined): string {
  const notes = items.map((item) => item.note);
  const where = project ? `${project} ` : "";
  switch (kind.key) {
    case "tests": {
      const backend = notes.filter((note) => /\bbackend\b/i.test(note)).length * 2 > notes.length;
      return `Run the full ${where}${backend ? "backend" : "test"} suite and report any failures`;
    }
    case "gates":
      return "Collect the day’s backend and frontend test gates and say which tasks are ready to commit";
    case "ci": {
      // An `Owner/repo`, the way GitHub writes one. The owner must read as a name ("Sadeef"), or
      // "CI/deploy" and "backend/frontend" — both in his notes — would pass for repositories.
      const repo = mostCommon(notes.map((note) => /\b([A-Z][a-z][\w-]*\/[\w.-]*\w)\b/.exec(note)?.[1]));
      return repo
        ? `Check GitHub Actions on ${repo} and report anything red`
        : `Check the latest ${where}GitHub Actions runs and report anything red`;
    }
    case "alarm":
      return `Sound the ${clock(new Date(items[0].fire_at))} alarm`;
    default:
      return [...notes].sort((a, b) => a.length - b.length)[0];
  }
}

/** "Based on 10 reminders · Aug 6 and Aug 12", "Based on 9 reminders · Aug 7 to Aug 11", "Set once on Aug 30". */
function basisFor(items: readonly ReminderLike[]): string {
  const days = [...new Set([...items].sort((a, b) => ms(a.fire_at) - ms(b.fire_at)).map((item) => shortDate(new Date(item.fire_at))))];
  if (items.length === 1) return `Set once on ${days[0]}`;
  const when = days.length === 1 ? days[0] : days.length === 2 ? `${days[0]} and ${days[1]}` : `${days[0]} to ${days[days.length - 1]}`;
  return `Based on ${items.length} reminders · ${when}`;
}

/** A reminder's own local clock, `HH:MM`. */
function localClock(iso: string): string {
  const at = new Date(iso);
  return clockText(at.getHours(), at.getMinutes());
}

/** When these usually fired: the median local time of day, to the nearest half hour. */
export function typicalClock(isos: readonly string[]): string {
  const minutes = isos
    .map((iso) => new Date(iso))
    .filter((at) => !Number.isNaN(at.getTime()))
    .map((at) => at.getHours() * 60 + at.getMinutes())
    .sort((a, b) => a - b);
  if (minutes.length === 0) return "09:00";
  const mid = Math.floor(minutes.length / 2);
  const median = minutes.length % 2 ? minutes[mid] : (minutes[mid - 1] + minutes[mid]) / 2;
  const rounded = (Math.round(median / 30) * 30) % (24 * 60);
  return clockText(Math.floor(rounded / 60), rounded % 60);
}

function mostCommon(values: readonly (string | undefined)[]): string | undefined {
  const counts = new Map<string, number>();
  for (const value of values) if (value) counts.set(value, (counts.get(value) ?? 0) + 1);
  let best: string | undefined;
  for (const [value, n] of counts) if (!best || n > (counts.get(best) ?? 0)) best = value;
  return best;
}
