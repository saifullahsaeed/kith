/**
 * How the whole app says when something happened.
 *
 * This existed three times over — once in the history panel, once in the inbox, once in the
 * control panel's `format.ts` — and the three had drifted, which is the ordinary fate of a
 * helper small enough to retype. The same afternoon read "Saturday, August 9" in one panel and
 * "Saturday, 9 Aug" in the next, and the same instant read `02:26` beside `2:26`. Nothing was
 * broken; the app just spoke about time in three accents.
 *
 * The version kept is the history panel's, and it was the right one on the merits rather than
 * by seniority: it is the only one that checked for an unparseable date, and the only one that
 * compared *midnights* instead of subtracting milliseconds or matching `toDateString()` — both
 * of which put "Yesterday" on the wrong day across a daylight-saving boundary.
 */

/** "Today", "Yesterday", a weekday within the week, then a date. */
export function dayLabel(iso: string): string {
  try {
    const at = new Date(iso);
    if (Number.isNaN(at.getTime())) return "Undated";
    const midnight = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    const now = new Date();
    const days = Math.round((midnight(now) - midnight(at)) / 86_400_000);
    if (days <= 0) return "Today";
    if (days === 1) return "Yesterday";
    if (days < 7) return at.toLocaleDateString(undefined, { weekday: "long" });
    if (at.getFullYear() === now.getFullYear())
      return at.toLocaleDateString(undefined, { day: "numeric", month: "long" });
    return at.toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" });
  } catch {
    return "Undated";
  }
}

/** Group timestamped items into [dayLabel, items][], preserving input order. */
export function groupByDay<T>(items: T[], getIso: (item: T) => string): [string, T[]][] {
  const groups = new Map<string, T[]>();
  for (const item of items) {
    const key = dayLabel(getIso(item));
    const list = groups.get(key);
    if (list) list.push(item);
    else groups.set(key, [item]);
  }
  return [...groups.entries()];
}

/** Clock time alone, for a line whose day is already established by its heading. */
export function time(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

/** Date and time together, for a line that stands on its own. */
export function when(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "";
  }
}
