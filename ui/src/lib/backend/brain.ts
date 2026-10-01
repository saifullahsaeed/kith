/** Client for the control panel — reading and curating Kith's whole database. */

export interface Memory {
  id: number;
  content: string;
  tags: string[];
  importance: number;
  level: string;
  created_at: string;
}
export interface JournalEntry {
  id: number;
  entry: string;
  created_at: string;
}
export interface Task {
  id: number;
  goal: string;
  status: string;
  priority: string; // low | normal | high
  description: string;
  created_by: string; // kith | user
  project_id: number | null;
  created_at: string;
  updated_at: string;
  /** Checklist progress and how many outputs it has filed — what a board card shows. Sent with
   *  the snapshot (`/api/brain`, `/api/projects/:id/tasks`), absent on rows from anywhere else. */
  checklist_done?: number;
  checklist_total?: number;
  outputs?: number;
}
export interface TaskComment {
  id: number;
  task_id: number;
  author: string; // kith | user
  body: string;
  created_at: string;
}
export interface ChecklistItem {
  id: number;
  task_id: number;
  text: string;
  done: number;
  order_index: number;
  created_at: string;
}
export interface Deliverable {
  id: number;
  task_id: number;
  kind: string; // text | file | link
  title: string;
  content: string;
  created_at: string;
  /** Size on disk, for a `file`. Absent when the file has been moved or cannot be read —
   *  which is a different thing from empty, and reads differently. */
  bytes?: number;
  /** When the file itself last changed, ISO. Not the same fact as `created_at`, which is when
   *  the deliverable was filed: he can rewrite the document ten times without refiling it. */
  modified?: string;
}
export interface TaskDetail extends Task {
  comments: TaskComment[];
  checklist: ChecklistItem[];
  deliverables: Deliverable[];
  /** The plan this task was approved on, as Markdown — read from `.kith/work/task-<id>.md` on
   *  every request, so the file is still the thing you edit. Empty when none is filed. */
  plan: string;
}
export interface Project {
  id: number;
  name: string;
  description: string;
  status: string; // active | done | paused | archived
  /**
   * The folder this project's work happens in, or null for a project with no files.
   *
   * The server has always sent it and nothing here declared it, so the one fact that decides
   * where every file he writes lands was invisible in the interface. That is how a project
   * spent a run pointed at a folder that had been deleted, and how `job/the-app` quietly
   * resolved to an empty `~/Kith/job/the-app` instead of the codebase on the Desktop —
   * both diagnosable in a second if the path had been on screen.
   */
  directory: string | null;
  created_at: string;
  updated_at: string;
  /** How many of its tasks sit in each column. `tasks_active` is approved + working — what he
   *  may pick up; `tasks_total` counts dropped work too. */
  tasks_planning: number;
  tasks_approved: number;
  tasks_working: number;
  tasks_done: number;
  tasks_dropped: number;
  tasks_active: number;
  tasks_total: number;
}
/** One thing a project produced, from any of its tasks — `GET /api/projects/:id/outputs`. */
export interface ProjectOutput extends Deliverable {
  /** The goal of the task that filed it, so a list of outputs can say where each came from. */
  task_goal: string;
}
/* Restored. Commit 3d18366 ("Remove the tools he wrote for himself") deleted 53 lines from
 * this file to take out `CustomTool`, and took these five with it — all of them still
 * referenced by `BrainSnapshot` below, and none of them declared anywhere else. That left
 * `tsc` failing on five undeclared names, which meant `npm run build` (`tsc -b && vite build`)
 * never reached vite: the app still ran, because the dev server transpiles without checking
 * types, so nothing said anything until a build was asked for.
 *
 * Taken verbatim from 3d18366^ rather than rewritten from how they are used. These describe
 * rows the server sends, and guessing their shape from the three fields the panel happens to
 * read is how a type stops matching the thing it is typing. */

export interface Reminder {
  id: number;
  fire_at: string;
  note: string;
  status: string;
  created_at: string;
  fires: string;
}

export interface Message {
  id: number;
  body: string;
  kind: string;
  read: number;
  /** An in-app path like "/tasks/42" when this is about something you can open. */
  link: string | null;
  sender: string;
  created_at: string;
}

export interface Source {
  id: number;
  title: string;
  origin: string;
  created_at: string;
  chars: number;
  /** How many passages he stored it as, when the server says. `list_sources` does not yet, so
   *  the Sources screen leaves that number out rather than guess it from the length. */
  passages?: number;
}

/** One source in full — `GET /api/sources/<id>`. */
export interface SourceDetail extends Source {
  content: string;
  project_id: number | null;
}

export interface Schedule {
  id: number;
  note: string;
  /** Which chat this standing job wakes. Null for one bound to nothing, which can wake any. */
  conversation_id: string | null;
  every_minutes: number | null;
  daily_at: string | null;
  next_fire: string;
  last_fired: string | null;
  status: string;
  created_at: string;
  fires: string;
}

export interface BrainSnapshot {
  counts: Record<string, number>;
  memories: Memory[];
  journal: JournalEntry[];
  tasks: Task[];
  reminders: Reminder[];
  messages: Message[];
  sources: Source[];
  schedules: Schedule[];
  projects: Project[];
}

export type TimelineKind = "memory" | "journal" | "task" | "tool" | "reminder" | "message";
export interface TimelineEvent {
  kind: TimelineKind;
  at: string;
  text: string;
  meta: Record<string, unknown>;
}

export async function fetchBrain(): Promise<BrainSnapshot> {
  const res = await fetch("/api/brain");
  if (!res.ok) throw new Error(`/api/brain ${res.status}`);
  return (await res.json()) as BrainSnapshot;
}

export async function fetchTimeline(): Promise<TimelineEvent[]> {
  const res = await fetch("/api/brain/timeline");
  if (!res.ok) throw new Error(`/api/brain/timeline ${res.status}`);
  return (await res.json()) as TimelineEvent[];
}

export async function deleteBrainItem(kind: string, key: string | number): Promise<void> {
  const res = await fetch(`/api/brain/${kind}/${encodeURIComponent(String(key))}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`delete ${kind}/${key} failed`);
}

export async function setMemoryLevel(id: number, level: string): Promise<void> {
  const res = await fetch(`/api/brain/memory/${id}/level`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ level }),
  });
  if (!res.ok) throw new Error("set level failed");
}

export async function createBrainItem(kind: string, data: Record<string, unknown>): Promise<void> {
  const res = await fetch(`/api/brain/${kind}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(`create ${kind} failed`);
}

export async function updateBrainItem(
  kind: string,
  key: string | number,
  data: Record<string, unknown>,
): Promise<void> {
  const res = await fetch(`/api/brain/${kind}/${encodeURIComponent(String(key))}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(`update ${kind} failed`);
}

export interface WorkspaceEntry {
  name: string;
  type: "dir" | "file";
  size: number;
  /** Seconds since the epoch. Zero when the container couldn't say. */
  modified: number;
}

export async function fetchWorkspace(
  path: string,
): Promise<{ path: string; entries: WorkspaceEntry[] }> {
  const res = await fetch(`/api/workspace?path=${encodeURIComponent(path)}`);
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(err.error || "couldn't list that folder");
  }
  return (await res.json()) as { path: string; entries: WorkspaceEntry[] };
}

/** One of the files a name turned out to match. */
export interface Candidate {
  /** How to show it — guaranteed by the server to differ from the others in the list. */
  name: string;
  /** Absolute, and what to ask for to open this one rather than another. */
  path: string;
}

/**
 * A name that matched several files.
 *
 * Its own class because the answer to it is a list, not a sentence. The server refuses rather
 * than guessing — opening the wrong `system.json` silently is worse than asking — and the
 * question it asks, "say which one", was unanswerable in the interface: the path came out of
 * his prose, so there is nothing for a person to correct. Carrying the matches lets the viewer
 * offer them.
 */
export class AmbiguousPath extends Error {
  // Declared and assigned rather than a constructor parameter property: this project builds
  // with `erasableSyntaxOnly`, which rules those out.
  candidates: Candidate[];

  constructor(message: string, candidates: Candidate[]) {
    super(message);
    this.name = "AmbiguousPath";
    this.candidates = candidates;
  }
}

export async function fetchWorkspaceFile(
  path: string,
  projectId?: number | null,
): Promise<{ path: string; content: string }> {
  const q = new URLSearchParams({ path });
  if (projectId != null) q.set("projectId", String(projectId));
  const res = await fetch(`/api/workspace/file?${q}`);
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as {
      error?: string;
      candidates?: Candidate[];
    };
    const said = err.error || "couldn't read that file";
    if (err.candidates?.length) throw new AmbiguousPath(said, err.candidates);
    throw new Error(said);
  }
  return (await res.json()) as { path: string; content: string };
}

/** Write a file back, for the one surface that edits one: the design editor. */
export async function saveWorkspaceFile(
  path: string,
  content: string,
  projectId?: number | null,
): Promise<{ path: string }> {
  const res = await fetch("/api/workspace/file", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, content, projectId: projectId ?? undefined }),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(err.error || "couldn't save that file");
  }
  return (await res.json()) as { path: string };
}

/** The design system a screen is drawn in — resolved server-side, where every folder is
 *  reachable. An empty path is the ordinary answer for a workspace with no system. */
export async function fetchDesignSystem(
  projectId?: number | null,
): Promise<{ path: string; content: string; conventions: string }> {
  const q = new URLSearchParams();
  if (projectId != null) q.set("projectId", String(projectId));
  const res = await fetch(`/api/design-system?${q}`);
  if (!res.ok) return { path: "", content: "", conventions: "" };
  // `conventions` is prose from a file beside the system. It cannot be derived from `content`,
  // which is why the route returns it rather than leaving the interface to find it.
  const body = (await res.json()) as { path: string; content: string; conventions?: string };
  return { ...body, conventions: body.conventions ?? "" };
}

/** What he last asked you to look at — a board, so far — or null. */
export async function fetchShowing(): Promise<{
  kind: string;
  taskId: number;
  note: string;
  at: string;
  conversation: string;
} | null> {
  const res = await fetch("/api/showing");
  if (!res.ok) return null;
  return ((await res.json()) as { showing: never }).showing ?? null;
}

/** Take it, so relaunching the app does not reopen a board from last Tuesday. */
export async function takeShowing(): Promise<void> {
  await fetch("/api/showing", { method: "DELETE" }).catch(() => {});
}

export async function fetchProjectOutputs(projectId: number): Promise<ProjectOutput[]> {
  const res = await fetch(`/api/projects/${projectId}/outputs`);
  if (!res.ok) throw new Error(`/api/projects/${projectId}/outputs ${res.status}`);
  return ((await res.json()) as { outputs: ProjectOutput[] }).outputs;
}

export async function fetchTaskDetail(id: number): Promise<TaskDetail> {
  const res = await fetch(`/api/tasks/${id}`);
  if (!res.ok) throw new Error(`/api/tasks/${id} ${res.status}`);
  return (await res.json()) as TaskDetail;
}

/** The full stored text of one source, which the snapshot leaves out — a page can be 20,000
 *  characters and the list only needs to say how many. Null when there is no such source: the
 *  route answers `{}` for that rather than a 404. */
export async function fetchSource(id: number): Promise<SourceDetail | null> {
  const res = await fetch(`/api/sources/${id}`);
  if (!res.ok) throw new Error(`/api/sources/${id} ${res.status}`);
  const body = (await res.json()) as Partial<SourceDetail>;
  if (body.id == null) return null;
  const content = body.content ?? "";
  return {
    id: body.id,
    title: body.title ?? "",
    origin: body.origin ?? "",
    created_at: body.created_at ?? "",
    chars: Array.from(content).length,
    content,
    project_id: body.project_id ?? null,
  };
}

/** Feed Kith a link or pasted text to read and remember. */
export async function ingestSource(input: {
  url?: string;
  text?: string;
  title?: string;
}): Promise<void> {
  const res = await fetch("/api/sources", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(err.error || "couldn't ingest that");
  }
}

/**
 * Just the projects — names, statuses, folders.
 *
 * `fetchBrain` is the whole database: 303KB here, of which 204KB is the journal and 75KB is tasks.
 * A list that groups conversations by project needs 5.5KB of that. It did not matter while the
 * panel asked once; it started mattering when `task` became a change that invalidates the snapshot,
 * because a turn ticking checklist items then re-sent a third of a megabyte per tick for the
 * browser to parse and discard.
 */
export async function fetchProjects(): Promise<Project[]> {
  const response = await fetch("/api/projects");
  if (!response.ok) throw new Error(`/api/projects returned ${response.status}`);
  const body = (await response.json()) as { projects?: Project[] };
  return body.projects ?? [];
}

/** Just the standing jobs. `fetchBrain` is the whole database; the Work panel needs this array. */
export async function fetchSchedules(): Promise<Schedule[]> {
  const response = await fetch("/api/schedules");
  if (!response.ok) throw new Error(`/api/schedules returned ${response.status}`);
  const body = (await response.json()) as { schedules?: Schedule[] };
  return body.schedules ?? [];
}
