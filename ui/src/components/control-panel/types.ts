
export type Tab =
  | "overview"
  | "lifetime"
  | "memory"
  | "journal"
  | "projects"
  | "reminders"
  | "schedules"
  | "sources"
  | "workspace";

/** Work lives in one place: Projects → a project → its tasks. Tasks with no
 * project sit in the "No project" tray, addressed by this sentinel. */
export const LOOSE = "none" as const;
export type ProjectRef = number | typeof LOOSE;

/** A borderless field that lives inside a <Composer> pill. */
export const FIELD = "min-w-0 bg-transparent text-sm outline-none placeholder:text-muted-foreground/55";
/** A bordered field for inline forms (edit, filters). */
export const INPUT =
  "rounded-lg border bg-card/40 px-3 py-1.5 text-sm outline-none transition-colors placeholder:text-muted-foreground/55 focus-visible:border-ring focus-visible:bg-card focus-visible:ring-[3px] focus-visible:ring-ring/25";

/**
 * What is happening, as colour.
 *
 * `CHIP` says which *domain* a page belongs to; this says what *state* a thing is in. They are
 * different jobs and they were being done by the same handful of hues, which is why neither read
 * as meaning anything.
 *
 * The rule: **amber is attention — his or yours; green is settled.** Amber had been carrying
 * "he may act", "done", "hover", "in progress" and "finished" simultaneously, and four
 * off-system hues (violet, orange, sky, emerald) had leaked in beside it. A colour with eleven
 * jobs carries no information.
 *
 * "Your turn" is deliberately weight rather than a third hue: a work screen that needs three
 * accents to say three things will need five to say five.
 */
export const STATE = {
  /** He is acting on it now — pair with a pulsing `bg-kith` dot. */
  doing: "text-kith",
  /** Nothing in the way; he may pick it up. */
  ready: "text-kith",
  /** Waiting on a decision from you. */
  yours: "text-foreground font-medium",
  settled: "text-roam",
  /** Blocked is not an error — it is the ordinary state of most of a roadmap. */
  held: "text-muted-foreground",
  failed: "text-destructive",
} as const;

/** Signature colour per domain — a neutral chip carrying a coloured glyph, so
 * colour reads as a quiet wayfinding cue rather than a block of fill. */
export const CHIP: Record<string, string> = {
  violet: "bg-muted/70 text-violet-500",
  sky: "bg-muted/70 text-sky-500",
  orange: "bg-muted/70 text-orange-500",
  lime: "bg-muted/70 text-lime-500",
  kith: "bg-muted/70 text-kith",
};

// Mirrors TASK_STATUSES on the server — see the note in task-detail.tsx.
export const TASK_STATUSES = ["planning", "approved", "working", "done", "dropped"];
export const PROJECT_STATUSES = ["active", "done", "paused", "archived"];
/** How each kind reads in a "Delete this …?" question. */
export const KIND_LABEL: Record<string, string> = {
  memory: "memory",
  note: "note",
  journal: "journal entry",
  task: "task",
  project: "project",
  milestone: "milestone",
  reminder: "reminder",
  schedule: "schedule",
  message: "message",
  person: "person",
  source: "source",
  deliverable: "deliverable",
  checklist_item: "step",
};

/** Maps each overview stat / nav entry to the tab it opens. Tasks land on
 * Projects, since that's where work lives now. */
export const TAB_FOR: Record<string, Tab> = {
  Memories: "memory",
  Journal: "journal",
  Projects: "projects",
  Tasks: "projects",
  Reminders: "reminders",
  Schedules: "schedules",
  Sources: "sources",
};

export type Handlers = {
  /** Re-read the snapshot. The roadmap graph needs it: a new dependency changes which
   *  tasks are available, and the board beside it would otherwise be stale. */
  refresh: () => void;
  /** True when the thing was actually deleted. Callers that navigate on delete have to wait
   *  for this: firing `onBack()` on the same tick opened the confirm over a page you had
   *  already left, and Cancel still lost your place. */
  remove: (kind: string, key: string | number, label: string) => Promise<boolean>;
  relevel: (id: number, level: string) => void;
  create: (kind: string, data: Record<string, unknown>) => void;
  update: (kind: string, key: string | number, data: Record<string, unknown>) => void;
};
