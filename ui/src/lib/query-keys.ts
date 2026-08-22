/**
 * What the interface holds, named — and what each kind of change makes stale.
 *
 * This is the piece the app did not have. There was a push channel saying "tasks changed" and
 * seven widgets subscribed to it, and each one then refetched *its own* endpoint into *its own*
 * `useState`. So a turn that filed three tasks fired three events, and each event ran the board's
 * refetch, the roadmap's refetch and the task detail's refetch separately, none of them deduping
 * against another or against a request already in flight. Meanwhile eleven other widgets ignored
 * the channel entirely and polled on clocks between 1.2 and 30 seconds, which is why one part of
 * the screen could agree with the server and the part beside it not.
 *
 * A cache fixes both halves at once, but only if there is one place that says which cached thing a
 * change invalidates. That place is `STALE_ON` below. Adding a widget is then a `useQuery` with a
 * key from here; adding a *kind* is a line here; and nothing anywhere owns a timer.
 *
 * Keys are arrays because a prefix invalidates everything under it: invalidating `["task"]` reaches
 * `["task", 108]` without anyone listing the ids. That property is what lets these stay this short.
 */

import type { ChangeKind } from "@/lib/backend/events";

/** Every key the app fetches under. One factory, so a typo is a type error rather than a miss. */
export const keys = {
  /** The board: projects, tasks, milestones — everything `GET /api/brain` answers with. */
  brain: () => ["brain"] as const,
  /** The lifetime event log. A second endpoint, so a second key — one key must mean one shape. */
  timeline: () => ["timeline"] as const,
  /** One project's roadmap graph. */
  roadmap: (projectId: number) => ["roadmap", projectId] as const,
  /** One task, in the detail panel. */
  task: (taskId: number) => ["task", taskId] as const,
  /** The history list.
   *
   * Keyed by the active conversation as well as the page size, because the answer genuinely differs
   * by it: opening one moves it to the top of the list and starting one adds it. A key that says so
   * gets the right list from cache when you go back to a conversation instead of refetching for it. */
  conversations: (limit: number, activeId: string) => ["conversations", limit, activeId] as const,
  /** The inbox. */
  messages: () => ["messages"] as const,
  /** What he is doing, as a status rather than as the line-by-line feed. */
  activityStatus: () => ["activity", "status"] as const,
  /** The feed's backlog — the snapshot a window opens with, before the stream takes over. */
  activityRecent: () => ["activity", "recent"] as const,
  /** Mode, pending requests and standing grants, together as the endpoint answers them. */
  permissions: () => ["permissions"] as const,
  /** The question this conversation is waiting on, if any. */
  question: (conversationId: string) => ["question", conversationId] as const,
  /** Long-running work started from this conversation. */
  processes: (conversationId: string) => ["processes", conversationId] as const,
  /** The task this conversation is working through. */
  workingOn: (conversationId: string) => ["workingOn", conversationId] as const,
  /** One folder's listing in his workspace. */
  workspace: (path: string) => ["workspace", path] as const,
} as const;

/** A key prefix — what `invalidateQueries` matches on. */
type Prefix = readonly (string | number | null)[];

/**
 * Which keys a kind of change makes stale.
 *
 * Written as prefixes and deliberately generous. A `project` event invalidates the history list
 * because a conversation group is named after a project, and getting that wrong is not a crash —
 * it is a panel that shows a project's old name until something else happens to move, which is
 * exactly the class of bug this whole change exists to end. Refetching one list too many costs a
 * request; refetching one too few costs trust in the screen.
 *
 * Every kind in `ChangeKind` must appear here, and `test_every_kind_reaches_the_cache` in
 * `use-live.test.ts` asserts it: a kind with no entry is a change nothing responds to, which reads
 * from the interface as a feature that is merely quiet.
 */
export const STALE_ON: Record<ChangeKind, Prefix[]> = {
  // A turn starting or finishing changes what he is doing and what he is working through.
  turn: [["activity", "status"], ["workingOn"]],
  // Tasks are on the board, in the roadmap, in the detail panel and in the working-on card.
  task: [["brain"], ["task"], ["roadmap"], ["workingOn"], ["timeline"]],
  // A project's name and status reach the board, its roadmap, and the history panel's groups.
  project: [["brain"], ["roadmap"], ["conversations"], ["timeline"]],
  // The inbox badge, and the board's own count of what is waiting on you.
  message: [["messages"], ["brain"], ["timeline"]],
  process: [["processes"]],
  workspace: [["workspace"]],
  question: [["question"]],
  permission: [["permissions"]],
};
