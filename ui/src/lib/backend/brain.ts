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
  /** Which milestone delivers this. Null for a one-off errand, which is never gated by the
   *  roadmap — see active_tasks on the server. */
  milestone_id: number | null;
  created_at: string;
  updated_at: string;
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
}
export interface TaskDetail extends Task {
  /** The milestone's title, for showing it without another lookup. */
  milestone_title: string | null;
  /** Titles of the unfinished milestones holding this back. Empty when he can work on it —
   *  status alone cannot tell you this, and the page would otherwise say "planned" about
   *  something he will not touch. */
  held_by: string[];
  comments: TaskComment[];
  checklist: ChecklistItem[];
  deliverables: Deliverable[];
  /** The plan this task was approved on, as Markdown — read from `.kith/work/task-<id>.md` on
   *  every request, so the file is still the thing you edit. Empty when none is filed. */
  plan: string;
}
export interface Milestone {
  id: number;
  project_id: number;
  title: string;
  target_at: string | null;
  status: string; // todo | done
  order_index: number;
  /** Milestone ids this one waits for. */
  waits_for: number[];
  /** Titles of the unfinished ones — the reason, not just the state. */
  blocked_by: string[];
  /** Nothing in the way and not done: this is what he may work on. */
  ready: boolean;
  tasks_total: number;
  tasks_done: number;
  tasks_active: number;
  /** In `working` — where he is right now. */
  tasks_doing: number;
  tasks_waiting: number;
  created_at: string;
  updated_at: string;
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
  milestones: Milestone[];
  milestones_done: number;
  milestones_total: number;
  tasks_active: number;
  tasks_total: number;
}
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
}
export interface Schedule {
  id: number;
  note: string;
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
  mood?: { label: string; energy: number; note: string; updated_at: string | null };
  self?: { identity: string; profile: string; updated_at: string | null };
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

export async function fetchWorkspaceFile(
  path: string,
  projectId?: number | null,
): Promise<{ path: string; content: string }> {
  const q = new URLSearchParams({ path });
  if (projectId != null) q.set("projectId", String(projectId));
  const res = await fetch(`/api/workspace/file?${q}`);
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(err.error || "couldn't read that file");
  }
  return (await res.json()) as { path: string; content: string };
}

export async function fetchTaskDetail(id: number): Promise<TaskDetail> {
  const res = await fetch(`/api/tasks/${id}`);
  if (!res.ok) throw new Error(`/api/tasks/${id} ${res.status}`);
  return (await res.json()) as TaskDetail;
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
