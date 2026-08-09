/** How the Control Panel renders values: paths, names, search matching.
 *
 *  Dates are not here. They were, and having them here is what let two other panels grow their
 *  own — a chat panel is not going to reach into `control-panel/` for a timestamp. They live in
 *  `@/lib/dates` now, where anything can ask for them. */
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

/** How a project (or the loose tray) reads in a breadcrumb. */
export function projectLabel(snap: BrainSnapshot, ref: ProjectRef): string {
  if (ref === LOOSE) return "No project";
  return (snap.projects ?? []).find((p) => p.id === ref)?.name ?? "Project";
}
