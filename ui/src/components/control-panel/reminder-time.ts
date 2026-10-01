/**
 * Time, as the Reminders and Schedules screens say it.
 *
 * Pure functions of `(target, now)` so the composer's preview — "Fires today at 3:30 PM — in 30
 * minutes" — and what is sent to the server are worked out by the same code, and both can be
 * tested without a clock.
 *
 * Times inside a sentence are formatted `en-US` on purpose. The sentences around them are English
 * ("today at", "in 30 minutes"), and a locale's own clock dropped into one ("15:30 Uhr") reads as
 * a typo. Cadences ("Every day · 09:00") are 24-hour, as the server stores them.
 */

const MINUTE = 60_000;
const DAY = 86_400_000;

/* ── The composer's quick times ─────────────────────────────────────────── */

export type QuickTime = "30m" | "2h" | "tonight" | "tomorrow" | "pick";

export const QUICK_TIMES: readonly { value: QuickTime; label: string }[] = [
  { value: "30m", label: "In 30 min" },
  { value: "2h", label: "In 2 hours" },
  { value: "tonight", label: "Tonight 9 PM" },
  { value: "tomorrow", label: "Tomorrow 9:00" },
  { value: "pick", label: "Pick a time…" },
];

/** A hand-picked moment: a `YYYY-MM-DD` day and an `HH:MM` clock, both in the reader's zone. */
export interface PickedTime {
  date: string;
  time: string;
}

/**
 * When a quick choice fires. Null only for "pick" without a readable day and time.
 *
 * "Tonight 9 PM" after nine rolls to tomorrow night rather than refusing — the preview then says
 * "tomorrow at 9:00 PM", which is the truth, where a disabled chip would just be a puzzle.
 */
export function resolveQuickTime(choice: QuickTime, now: Date, picked?: PickedTime): Date | null {
  switch (choice) {
    case "30m":
      return new Date(now.getTime() + 30 * MINUTE);
    case "2h":
      return new Date(now.getTime() + 120 * MINUTE);
    case "tonight": {
      const at = atClock(now, 21, 0, 0);
      if (at.getTime() <= now.getTime()) at.setDate(at.getDate() + 1);
      return at;
    }
    case "tomorrow":
      return atClock(now, 9, 0, 1);
    case "pick":
      return picked ? fromPicked(picked) : null;
  }
}

/** A local day and clock as a moment. Null when either half does not parse. */
export function fromPicked({ date, time }: PickedTime): Date | null {
  const day = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  const clock = parseClock(time);
  if (!day || !clock) return null;
  return new Date(Number(day[1]), Number(day[2]) - 1, Number(day[3]), clock.hour, clock.minute);
}

/**
 * What the server is sent for a choice.
 *
 * The relative ones go as minutes, which are exact whatever zone either end thinks it is in. The
 * absolute ones go as an instant (`at`, UTC ISO) rather than as a count of minutes from now: a
 * count is stale by however long the request takes, and a naive "21:00" would be read in the
 * *server's* idea of his zone, which need not be this window's.
 */
export function firePayload(choice: QuickTime, target: Date): { in_minutes: number } | { at: string } {
  if (choice === "30m") return { in_minutes: 30 };
  if (choice === "2h") return { in_minutes: 120 };
  return { at: target.toISOString() };
}

/** Whole minutes from now until `target`, never less than one. */
export function minutesUntil(target: Date, now: Date): number {
  return Math.max(1, Math.round((target.getTime() - now.getTime()) / MINUTE));
}

/**
 * Snoozing moves a reminder *later than it was*: from its own time, or from now if that has
 * already passed. "+30 minutes from now" on one due in 40 would have moved it earlier.
 */
export function snoozeTarget(fireAt: Date, now: Date, minutes: number): Date {
  const from = Math.max(fireAt.getTime(), now.getTime());
  return new Date(from + minutes * MINUTE);
}

/* ── Saying it ──────────────────────────────────────────────────────────── */

/** "3:30 PM". */
export function clock(at: Date): string {
  return at.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

/** "Aug 12". */
export function shortDate(at: Date): string {
  return at.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

/** "Wed". */
export function weekday(at: Date): string {
  return at.toLocaleDateString("en-US", { weekday: "short" });
}

/** "Thu 1 Oct" — day first, the way the boards write a date under a time. */
export function dayMonth(at: Date): string {
  return `${weekday(at)} ${at.getDate()} ${at.toLocaleDateString("en-US", { month: "short" })}`;
}

/** Whole local days from `now`'s midnight to `at`'s: 0 today, 1 tomorrow, -1 yesterday. */
export function dayOffset(at: Date, now: Date): number {
  const midnight = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  return Math.round((midnight(at) - midnight(now)) / DAY);
}

/** "today at 3:30 PM", "tomorrow at 9:00 AM", "Fri 2 Oct at 9:00 AM". */
export function dayAt(at: Date, now: Date): string {
  const days = dayOffset(at, now);
  const day =
    days === 0 ? "today" : days === 1 ? "tomorrow" : days === -1 ? "yesterday" : dayMonth(at);
  return `${day} at ${clock(at)}`;
}

/** The same, capitalised to start a line: "Today at 3:30 PM". */
export function dayAtStart(at: Date, now: Date): string {
  const text = dayAt(at, now);
  return text.charAt(0).toUpperCase() + text.slice(1);
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** "in 30 minutes", "in 2 hours", "in 1 hour 20 minutes", "in 3 days". */
export function untilLong(target: Date, now: Date): string {
  const minutes = Math.round((target.getTime() - now.getTime()) / MINUTE);
  if (minutes < 1) return "now";
  if (minutes < 60) return `in ${plural(minutes, "minute")}`;
  if (minutes < 24 * 60) {
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    return `in ${plural(hours, "hour")}${rest ? ` ${plural(rest, "minute")}` : ""}`;
  }
  return `in ${plural(Math.round(minutes / (24 * 60)), "day")}`;
}

/**
 * The countdown on an upcoming row: "in 30 min", "in 1 h 20 min", "in 3 days".
 *
 * A pending reminder whose time has passed says how late it is rather than "now" forever — one
 * the scheduler skipped (it only fires reminders bound to a chat) would otherwise look imminent
 * for the rest of its life.
 */
export function countdown(target: Date, now: Date): string {
  const minutes = Math.round((target.getTime() - now.getTime()) / MINUTE);
  if (minutes < -1) return `${short(-minutes)} late`;
  if (minutes < 1) return "now";
  return `in ${short(minutes)}`;
}

function short(minutes: number): string {
  if (minutes < 60) return `${minutes} min`;
  if (minutes < 24 * 60) {
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    return rest ? `${hours} h ${rest} min` : `${hours} h`;
  }
  return plural(Math.round(minutes / (24 * 60)), "day");
}

/** "just now", "12 min ago", "3 h ago", "yesterday", "Aug 12". */
export function agoPhrase(then: Date, now: Date): string {
  const minutes = Math.round((now.getTime() - then.getTime()) / MINUTE);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 24 * 60 && dayOffset(then, now) === 0) return `${Math.floor(minutes / 60)} h ago`;
  if (dayOffset(then, now) === -1) return "yesterday";
  return shortDate(then);
}

/** How far ahead a reminder was set: "Set 7 min ahead", "Set 4 h ahead". */
export function setAhead(createdAt: Date, fireAt: Date): string {
  const minutes = Math.max(0, Math.round((fireAt.getTime() - createdAt.getTime()) / MINUTE));
  return `Set ${short(minutes)} ahead`;
}

/**
 * A day's span of times: "9:30 AM", "8:47 – 10:35 PM", "7:48 AM – 3:01 PM".
 * The first half drops its AM/PM when both share it, the way people write a range.
 */
export function timeSpan(first: Date, last: Date): string {
  const a = clock(first);
  const b = clock(last);
  if (a === b) return a;
  const [aTime, aHalf] = a.split(" ");
  const [, bHalf] = b.split(" ");
  return aHalf === bHalf ? `${aTime} – ${b}` : `${a} – ${b}`;
}

/* ── Clock text, as typed ───────────────────────────────────────────────── */

/**
 * A clock typed by hand: "09:00", "9:00", "9", "930", "9:30 pm", "21:30". Null if it is not one.
 * The server's `daily_at` is a strict `HH:MM`; this is the forgiving half in front of it.
 */
export function parseClock(text: string): { hour: number; minute: number } | null {
  const match = /^\s*(\d{1,2})(?::?(\d{2}))?\s*([ap])?\.?m?\.?\s*$/i.exec(text);
  if (!match) return null;
  let hour = Number(match[1]);
  const minute = match[2] ? Number(match[2]) : 0;
  const half = match[3]?.toLowerCase();
  if (half) {
    if (hour < 1 || hour > 12) return null;
    hour = (hour % 12) + (half === "p" ? 12 : 0);
  }
  if (hour > 23 || minute > 59) return null;
  return { hour, minute };
}

/** `HH:MM`, zero-padded — the shape `daily_at` is stored in. */
export function clockText(hour: number, minute: number): string {
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

/** "09:00" as "9:00 AM", for a sentence. */
export function clockPhrase(hhmm: string): string {
  const parsed = parseClock(hhmm);
  if (!parsed) return hhmm;
  return clock(new Date(2000, 0, 1, parsed.hour, parsed.minute));
}

/** A local `YYYY-MM-DD`, for the date picker. */
export function isoDay(at: Date): string {
  return `${at.getFullYear()}-${String(at.getMonth() + 1).padStart(2, "0")}-${String(at.getDate()).padStart(2, "0")}`;
}

/** `at`'s day, at a clock time, `days` later. */
function atClock(at: Date, hour: number, minute: number, days: number): Date {
  return new Date(at.getFullYear(), at.getMonth(), at.getDate() + days, hour, minute);
}

/* ── Schedules ──────────────────────────────────────────────────────────── */

/** The first time a `daily_at` comes round after `now` — the same rule as the server's `next_fire_after`. */
export function nextDaily(hhmm: string, now: Date): Date | null {
  const parsed = parseClock(hhmm);
  if (!parsed) return null;
  const at = atClock(now, parsed.hour, parsed.minute, 0);
  if (at.getTime() <= now.getTime()) at.setDate(at.getDate() + 1);
  return at;
}

/** How a cadence reads on its badge: "Every day · 09:00", "Every 60 min". */
export function cadenceBadge(schedule: { daily_at: string | null; every_minutes: number | null }): string {
  if (schedule.daily_at) return `Every day · ${schedule.daily_at}`;
  if (schedule.every_minutes) return `Every ${everyPhrase(schedule.every_minutes)}`;
  return "No cadence";
}

/** "30 min", "hour", "2 h", "1 h 30 min", "day" — what follows "Every". */
export function everyPhrase(minutes: number): string {
  if (minutes === 60) return "hour";
  if (minutes % (24 * 60) === 0) return minutes === 24 * 60 ? "day" : `${minutes / (24 * 60)} days`;
  return short(minutes);
}

/**
 * The composer's line for a cadence, in two halves: the cadence itself (shown brighter) and when
 * its first run would be. Null halves mean the input does not parse yet.
 */
export function cadencePreview(
  cadence: { kind: "daily"; at: string } | { kind: "every"; minutes: number },
  now: Date,
): { cadence: string; first: string } | null {
  if (cadence.kind === "daily") {
    const next = nextDaily(cadence.at, now);
    if (!next) return null;
    const days = dayOffset(next, now);
    const first =
      days === 0 ? `first run today, ${clock(next)}` : `first run tomorrow, ${dayMonth(next)}`;
    return { cadence: `every day at ${clockPhrase(cadence.at)}`, first };
  }
  if (!Number.isFinite(cadence.minutes) || cadence.minutes <= 0) return null;
  const next = new Date(now.getTime() + cadence.minutes * MINUTE);
  return { cadence: `every ${everyPhrase(cadence.minutes)}`, first: `first run ${dayAt(next, now)}` };
}

/**
 * The next seven days of a daily schedule, today first: which days run and which is next.
 * A schedule on an interval runs many times a day, so it has no strip — the caller shows none.
 */
export function weekStrip(
  hhmm: string,
  nextFire: Date | null,
  now: Date,
  paused: boolean,
): { key: string; dow: string; date: number; run: boolean; next: boolean }[] {
  const strip = [];
  const nextDay = nextFire ? isoDay(nextFire) : null;
  // Start from the day of the next run when that is today or tomorrow, so the strip reads as
  // "the next seven days of runs" rather than leading with a day that has already had its run.
  const start = nextFire && dayOffset(nextFire, now) === 1 ? 1 : 0;
  for (let i = start; i < start + 7; i++) {
    const day = new Date(now.getFullYear(), now.getMonth(), now.getDate() + i);
    const key = isoDay(day);
    const parsed = parseClock(hhmm);
    const runAt = parsed ? new Date(day.getFullYear(), day.getMonth(), day.getDate(), parsed.hour, parsed.minute) : null;
    const run = !paused && runAt !== null && runAt.getTime() > now.getTime();
    strip.push({ key, dow: weekday(day), date: day.getDate(), run, next: run && key === nextDay });
  }
  return strip;
}
