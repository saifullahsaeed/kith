/** Client for a project's roadmap graph. */

export interface RoadmapNode {
  id: number;
  project_id: number;
  title: string;
  status: string;
  target_at: string | null;
  /** Hand-placed position. Null means nobody has arranged this node yet. */
  x: number | null;
  y: number | null;
  /** Milestone ids this one waits for. */
  waits_for: number[];
  /** Titles of the unfinished ones — the reason, not just the state. */
  blocked_by: string[];
  /** Nothing in the way and not yet done: this is what he may work on. */
  ready: boolean;
  tasks_total: number;
  tasks_done: number;
  tasks_active: number;
  /** In `doing` — where he is right now. The graph pulses this node. */
  tasks_doing: number;
  /** In `waiting` — he asked you something and stopped. */
  tasks_waiting: number;
}

export interface RoadmapEdge {
  milestone_id: number;
  depends_on_id: number;
}

export interface Roadmap {
  milestones: RoadmapNode[];
  dependencies: RoadmapEdge[];
}

const base = (projectId: number) => `/api/projects/${projectId}/roadmap`;

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))) as { error?: string };
    throw new Error(detail.error ?? `that didn't work (${response.status})`);
  }
  return (await response.json()) as T;
}

export const fetchRoadmap = (projectId: number) => json<Roadmap>(base(projectId));

export const addDependency = (projectId: number, milestoneId: number, dependsOnId: number) =>
  json<Roadmap>(`${base(projectId)}/dependencies`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ milestoneId, dependsOnId }),
  });

export const removeDependency = (projectId: number, milestoneId: number, dependsOnId: number) =>
  json<Roadmap>(
    `${base(projectId)}/dependencies?milestoneId=${milestoneId}&dependsOnId=${dependsOnId}`,
    { method: "DELETE" },
  );

export const savePositions = (
  projectId: number,
  positions: { id: number; x: number; y: number }[],
) =>
  json<Roadmap>(`${base(projectId)}/positions`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ positions }),
  });
