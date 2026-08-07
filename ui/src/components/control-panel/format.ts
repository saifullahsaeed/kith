/** How the Control Panel renders values: paths, dates, search matching. */
import type { BrainSnapshot } from "@/lib/backend/brain";

import { LOOSE } from "./types";
import type { ProjectRef } from "./types";

/**
 * A path at the width a card has, keeping the end that identifies it.
 *
 * The home prefix goes to `~`, and anything still too long loses its middle. Not the *start* —
 * `/Users/saifullahsaeed/Desktop/personal/` is the part that never varies between projects, and
 * `…/ai-play` is the part you are reading it for.
 *
 * The first attempt at this was `dir="rtl"` with CSS truncation, which is a neat trick and wrong:
 * a leading `/` is directionally neutral, so the browser reordered it to the end and
 * `/Users/…/ai-play` rendered as `Users/…/ai-play/` — an absolute path displayed as a relative one,
 * on the card whose whole job is telling you which folder this is.
 */
export function shortPath(path: string, keep = 34): string {
  const tidy = path.replace(/^\/Users\/[^/]+/, "~");
  if (tidy.length <= keep) return tidy;
  const parts = tidy.split("/").filter(Boolean);
  let tail = parts[parts.length - 1] ?? tidy;
  for (let i = parts.length - 2; i >= 0; i--) {
    const wider = `${parts[i]}/${tail}`;
    if (wider.length + 2 > keep) break;
    tail = wider;
  }
  return `…/${tail}`;
}

/* ── Helpers ────────────────────────────────────────────────────────────── */

export function clamp(n: number): number {
  return Math.max(0, Math.min(100, n));
}

export function initials(name: string): string {
  return (
    name
      .split(/\s+/)
      .filter(Boolean)
      .map((w) => w[0])
      .slice(0, 2)
      .join("")
      .toUpperCase() || "?"
  );
}

export function matches(query: string, ...fields: (string | undefined)[]): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return fields.some((f) => (f ?? "").toLowerCase().includes(q));
}

/** Group timestamped items into [dayLabel, items][] preserving input order. */
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

function dayLabel(iso: string): string {
  try {
    const d = new Date(iso);
    const today = new Date();
    const y = new Date();
    y.setDate(today.getDate() - 1);
    const same = (a: Date, b: Date) => a.toDateString() === b.toDateString();
    if (same(d, today)) return "Today";
    if (same(d, y)) return "Yesterday";
    return d.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
  } catch {
    return "Earlier";
  }
}

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

export function time(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

/** How a project (or the loose tray) reads in a breadcrumb. */
export function projectLabel(snap: BrainSnapshot, ref: ProjectRef): string {
  if (ref === LOOSE) return "No project";
  return (snap.projects ?? []).find((p) => p.id === ref)?.name ?? "Project";
}
