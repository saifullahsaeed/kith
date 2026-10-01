/** Days as the Journal and Lifetime screens say them.
 *
 * `@/lib/dates` is how the rest of the app says a time, and these two screens need things it
 * does not have: a local calendar day as a key ("2026-09-30" — the unit both screens group, tint
 * and jump by), a month laid out Monday-first, and the design's two date shapes,
 * "Wednesday 30 September" for a heading and "Wed 30 Sep" for a list. Both are composed by hand
 * rather than left to the locale, because `en-US` puts a comma and the month first
 * ("Wednesday, September 30") and the board is drawn the other way round. */

const DAY = 86_400_000;

/** Milliseconds for an ISO time, or 0 for one that does not parse — so it sorts last, not NaN. */
export function msOf(iso: string): number {
  const ms = new Date(iso).getTime();
  return Number.isNaN(ms) ? 0 : ms;
}

/** The local calendar day a moment falls on, as `YYYY-MM-DD`. Local, because "which day did he
 *  write this" is asked by someone looking at their own clock. */
export function dayKey(ms: number): string {
  const d = new Date(ms);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** Local midnight of a `YYYY-MM-DD` key. */
export function keyMidnight(key: string): number {
  const [y, m, d] = key.split("-").map(Number);
  return new Date(y!, m! - 1, d!).getTime();
}

/** Local midnight of the day `ms` falls on. */
export function midnight(ms: number): number {
  const d = new Date(ms);
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

/** Whole days between two moments' local midnights — via midnights, so a daylight-saving night
 *  is still one day and not 23/24ths of one. */
export function daysBetween(earlier: number, later: number): number {
  return Math.round((midnight(later) - midnight(earlier)) / DAY);
}

/** "Wednesday 30 September", with the year only when it is not this one. */
export function longDate(ms: number, now = Date.now()): string {
  const d = new Date(ms);
  const weekday = d.toLocaleDateString(undefined, { weekday: "long" });
  const month = d.toLocaleDateString(undefined, { month: "long" });
  const year = d.getFullYear() === new Date(now).getFullYear() ? "" : ` ${d.getFullYear()}`;
  return `${weekday} ${d.getDate()} ${month}${year}`;
}

/** "Wed 30 Sep". */
export function shortDate(ms: number): string {
  const d = new Date(ms);
  const weekday = d.toLocaleDateString(undefined, { weekday: "short" });
  const month = d.toLocaleDateString(undefined, { month: "short" });
  return `${weekday} ${d.getDate()} ${month}`;
}

/** "12:17 PM" — the hour unpadded, as the design writes it ("3:41 PM", not "03:41 PM"). */
export function clock(ms: number): string {
  if (!ms) return "";
  return new Date(ms).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

/** A month as one number, so "is this before that" is a comparison: year × 12 + month. */
export type MonthIndex = number;
export function monthIndex(ms: number): MonthIndex {
  const d = new Date(ms);
  return d.getFullYear() * 12 + d.getMonth();
}

/** "September 2026". */
export function monthTitle(year: number, month: number): string {
  const name = new Date(year, month, 1).toLocaleDateString(undefined, { month: "long" });
  return `${name} ${year}`;
}

/** How long ago a day was, said the way a person would: Today, Yesterday, 3 days ago, Last week,
 *  5 weeks ago, 4 months ago. */
export function relativeDay(ms: number, now = Date.now()): string {
  const days = daysBetween(ms, now);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return `${days} days ago`;
  if (days < 14) return "Last week";
  if (days < 60) return `${Math.floor(days / 7)} weeks ago`;
  const months = Math.floor(days / 30);
  return months < 12 ? `${months} months ago` : months < 24 ? "Last year" : `${Math.floor(months / 12)} years ago`;
}

/**
 * A month as calendar cells, Monday-first: `null` for the blanks before the 1st and after the
 * last day, so the grid is whole weeks. September 2026 starts on a Tuesday — one blank, thirty
 * days, four blanks.
 */
export function monthCells(year: number, month: number): (number | null)[] {
  const lead = (new Date(year, month, 1).getDay() + 6) % 7;
  const length = new Date(year, month + 1, 0).getDate();
  const cells: (number | null)[] = Array.from({ length: lead }, () => null);
  for (let day = 1; day <= length; day++) cells.push(day);
  while (cells.length % 7) cells.push(null);
  return cells;
}

/** The heat map's five steps: none, one, a few (2–3), several (4–7), a lot (8+). Steps rather
 *  than a ratio of the month's busiest day, so "1 Sep: 44 entries" does not wash every other day
 *  in the month out to nothing. */
export function heatLevel(count: number): 0 | 1 | 2 | 3 | 4 {
  if (count <= 0) return 0;
  if (count === 1) return 1;
  if (count <= 3) return 2;
  if (count <= 7) return 3;
  return 4;
}

/** "1 entry", "84 entries"; "1 thing", "487 things". */
export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString()} ${n === 1 ? one : many}`;
}
