/** Client for the control panel — reading and curating Kith's whole database. */

export interface Memory {
  id: number;
  content: string;
  tags: string[];
  importance: number;
  level: string;
  created_at: string;
}
export interface Note {
  id: number;
  title: string;
  body: string;
  created_at: string;
  updated_at: string;
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
  due_at: string | null;
  description: string;
  created_by: string; // kith | user
  project_id: number | null;
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
  comments: TaskComment[];
  checklist: ChecklistItem[];
  deliverables: Deliverable[];
}
export interface Milestone {
  id: number;
  project_id: number;
  title: string;
  target_at: string | null;
  status: string; // todo | done
  order_index: number;
  created_at: string;
  updated_at: string;
}
export interface Project {
  id: number;
  name: string;
  description: string;
  status: string; // active | done | paused | archived
  created_at: string;
  updated_at: string;
  milestones: Milestone[];
  milestones_done: number;
  milestones_total: number;
  tasks_active: number;
  tasks_total: number;
}
export interface CustomTool {
  name: string;
  description: string;
  language: string;
  code: string;
  created_at: string;
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
  read: number;
  created_at: string;
}
export interface Person {
  id: number;
  name: string;
  relationship: string;
  profile: string;
  created_at: string;
  updated_at: string;
}
export interface Curiosity {
  id: number;
  topic: string;
  note: string;
  status: string;
  created_at: string;
  updated_at: string;
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
  notes: Note[];
  journal: JournalEntry[];
  tasks: Task[];
  tools: CustomTool[];
  reminders: Reminder[];
  messages: Message[];
  people: Person[];
  curiosities: Curiosity[];
  sources: Source[];
  schedules: Schedule[];
  projects: Project[];
  mood?: { label: string; energy: number; note: string; updated_at: string | null };
  self?: { identity: string; profile: string; updated_at: string | null };
}

export type TimelineKind =
  "memory" | "note" | "journal" | "task" | "tool" | "reminder" | "message" | "curiosity";
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

export async function fetchWorkspaceFile(path: string): Promise<{ path: string; content: string }> {
  const res = await fetch(`/api/workspace/file?path=${encodeURIComponent(path)}`);
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
