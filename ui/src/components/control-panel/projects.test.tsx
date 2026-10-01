/**
 * The Projects list as the 2026-10-01 redesign draws it: grouped by what each project needs, the
 * summary tiles narrowing it, the empty boards folded into one line, and the old card grid still
 * reachable from the view toggle.
 */
import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/ui/confirm";
import type { ConversationSummary } from "@/lib/backend";
import type { BrainSnapshot } from "@/lib/backend/brain";
import { keys } from "@/lib/query-keys";
import { Projects } from "./projects";

type Project = BrainSnapshot["projects"][number];
type Task = BrainSnapshot["tasks"][number];

function project(id: number, name: string, status = "active"): Project {
  return {
    id,
    name,
    description: `About ${name}`,
    status,
    directory: `/Users/someone/Kith/${name.toLowerCase()}`,
    created_at: "2026-08-01T10:00:00Z",
    updated_at: `2026-09-${String(10 + id).padStart(2, "0")}T10:00:00Z`,
    tasks_planning: 0,
    tasks_approved: 0,
    tasks_working: 0,
    tasks_done: 0,
    tasks_dropped: 0,
    tasks_active: 0,
    tasks_total: 0,
  };
}

let id = 100;
const task = (projectId: number | null, status: string): Task => ({
  id: id++,
  goal: `Goal ${id}`,
  status,
  priority: "normal",
  description: "",
  created_by: "kith",
  project_id: projectId,
  created_at: "2026-09-01T10:00:00Z",
  updated_at: "2026-09-01T10:00:00Z",
});

function snapshot(): BrainSnapshot {
  return {
    counts: {},
    memories: [],
    journal: [],
    reminders: [],
    messages: [],
    sources: [],
    schedules: [],
    projects: [project(1, "Vigil"), project(2, "Sprout"), project(3, "Placeholder"), project(4, "Nightline", "archived")],
    tasks: [task(1, "planning"), task(1, "planning"), task(1, "done"), task(2, "working"), task(null, "planning")],
  };
}

const chats: ConversationSummary[] = [
  {
    id: "c1",
    title: "run this please",
    sessionId: "s",
    createdAt: "2026-09-22T10:00:00Z",
    updatedAt: "2026-09-22T10:00:00Z",
    messages: 4,
    lastSaid: "",
    transcript: "",
    projectId: 1,
    working: false,
    waiting: false,
  },
];

function renderProjects(onOpenProject = vi.fn()) {
  const cache = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  cache.setQueryData(keys.conversations(500, ""), chats);
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <ConfirmProvider>
          <Projects
            snap={snapshot()}
            query=""
            onOpenProject={onOpenProject}
            refresh={() => {}}
            remove={async () => false}
            relevel={() => {}}
            create={() => {}}
            update={() => {}}
          />
        </ConfirmProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return onOpenProject;
}

const rowNames = () =>
  [...document.querySelectorAll("[data-project-row]")].map((row) => row.querySelector("span span")?.firstChild?.textContent);

describe("the Projects list", () => {
  it("groups active projects by what they need, with the tray in its group and empty boards folded", () => {
    renderProjects();
    expect(screen.getByText("Needs you")).toBeInTheDocument();
    // Once as the column heading, once as the group.
    expect(screen.getAllByText("Working")).toHaveLength(2);
    // Vigil (2 plans) then the tray (1 plan) under Needs you; Sprout under Working.
    expect(rowNames()).toEqual(["Vigil", "No project", "Sprout"]);
    expect(screen.getByRole("button", { name: "Review all 3 plans" })).toBeInTheDocument();
    // The empty board is a chip on the folded line, not a row.
    expect(screen.getByText("No tasks and no conversations yet:")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Placeholder" })).toBeInTheDocument();
    expect(screen.getByText("run this please")).toBeInTheDocument();
  });

  it("narrows to the projects a tile counts when it is pressed, and back when pressed again", () => {
    renderProjects();
    const tile = screen.getByRole("button", { name: /Working now/ });
    fireEvent.click(tile);
    expect(tile).toHaveAttribute("aria-pressed", "true");
    expect(rowNames()).toEqual(["Sprout"]);
    fireEvent.click(tile);
    expect(rowNames()).toEqual(["Vigil", "No project", "Sprout"]);
  });

  it("unfolds the empty boards into rows", () => {
    renderProjects();
    fireEvent.click(screen.getByRole("button", { name: "Show as rows" }));
    expect(rowNames()).toContain("Placeholder");
    expect(screen.getByRole("button", { name: "Fold the empty boards" })).toBeInTheDocument();
  });

  it("counts each status tab and shows that tab's projects ungrouped", () => {
    renderProjects();
    const tabs = screen.getByRole("group", { name: "Which projects" });
    const archived = within(tabs).getByRole("button", { name: /Archived/ });
    expect(archived).toHaveTextContent("1");
    fireEvent.click(archived);
    expect(screen.queryByText("Needs you")).not.toBeInTheDocument();
    expect(screen.getByText("Nightline")).toBeInTheDocument();
  });

  it("opens a project from its row, and keeps the card grid one toggle away", () => {
    const open = renderProjects();
    fireEvent.click(document.querySelector("[data-project-row]")!);
    expect(open).toHaveBeenCalledWith(1);
    fireEvent.click(screen.getByRole("button", { name: "Grid view" }));
    expect(document.querySelector("[data-project-row]")).toBeNull();
    expect(screen.getByText("About Vigil")).toBeInTheDocument();
  });
});
