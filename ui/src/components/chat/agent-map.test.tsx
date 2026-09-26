/**
 * The agent map, drawn once for the window.
 *
 * It opens for whichever conversation asks — the title bar's pill, the Work panel, the thread's
 * workflow card — and has to put every agent in the phase it ran in and, on a click, show what no
 * list can: the prompt it was given and what it said back.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { act, fireEvent, render, screen } from "@/test/render";

import type { ActivityItem } from "@/lib/backend/activity";
import { useAgentMap } from "@/lib/agent-map";

const feed: { activity: ActivityItem[] } = { activity: [] };
vi.mock("@/hooks/use-activity", () => ({
  useActivity: () => ({ status: null, activity: feed.activity, refresh: () => undefined }),
}));

const { AgentMapHost } = await import("./agent-map");

const at = (s: number) => new Date(Date.UTC(2026, 8, 25, 10, 0, s)).toISOString();

function run(state: NonNullable<ActivityItem["workflow"]>["state"], text: string, extra = {}, s = 0): ActivityItem {
  return {
    kind: "workflow",
    text,
    at: at(s),
    conversation: "c1",
    workflow: { run: "wf-1", state, name: "devices-screen", ...extra },
  };
}

function agent(id: string, state: "running" | "step" | "done", extra: Record<string, unknown> = {}, s = 1): ActivityItem {
  return {
    kind: "errand",
    text: String(extra.text ?? id),
    at: at(s),
    conversation: "c1",
    errand: { id, state, objective: String(extra.objective ?? id), role: "scout", run: "wf-1", ...extra },
  };
}

beforeEach(() => {
  feed.activity = [];
  act(() => useAgentMap.getState().close());
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({ runs: [] })));
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the host", () => {
  it("draws nothing until a conversation asks for its map", () => {
    const { container } = render(<AgentMapHost />);
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText("Agent map")).toBeNull();
  });

  it("opens for the conversation that asked, whichever that is", () => {
    feed.activity = [run("started", "x")];
    render(<AgentMapHost />);
    act(() => useAgentMap.getState().show("c2"));
    expect(screen.getByText("Agent map")).toBeTruthy();
    expect(screen.getByText(/No workflow has run in this conversation yet/)).toBeTruthy();
  });
});

describe("the map", () => {
  beforeEach(() => {
    feed.activity = [
      run("started", "devices-screen", { phases: ["Survey", "Build", "Review"], description: "a devices screen" }),
      run("phase", "Survey"),
      agent("s1", "running", { objective: "conventions", phase: "Survey", prompt: "Survey the webapp conventions." }),
      agent("s1", "done", { text: "read_file x16", cost: 0.03, calls: 21, report: "Routes live in do_GET." }, 40),
      run("phase", "Build", {}, 41),
      agent("b1", "running", { objective: "discover-module", role: "builder", phase: "Build" }, 41),
      agent("b2", "running", { objective: "api-route", role: "builder", phase: "Build", after: [] }, 41),
      agent(
        "b2",
        "done",
        { role: "builder", text: "edit_files x2", patch: "/p/api-route.patch", outcome: "patch /p/api-route.patch (+12 −1)" },
        80,
      ),
    ];
  });

  it("opens from the chip and files every agent under its phase", () => {
    render(<AgentMapHost />);
    act(() => useAgentMap.getState().show("c1"));

    expect(screen.getByText("Agent map")).toBeTruthy();
    expect(screen.getByText("3 agents · click an agent for details")).toBeTruthy();
    for (const phase of ["Survey", "Build", "Review"]) expect(screen.getByText(phase)).toBeTruthy();
    expect(screen.getByText("conventions")).toBeTruthy();
    expect(screen.getByText("discover-module")).toBeTruthy();
    // A phase the plan declared but nothing has reached yet is shown, not dropped.
    expect(screen.getByText("not started")).toBeTruthy();
  });

  it("shows what an agent was told and what it said, on a click", () => {
    render(<AgentMapHost />);
    act(() => useAgentMap.getState().show("c1"));
    fireEvent.click(screen.getByRole("button", { name: /conventions/ }));

    expect(screen.getByText("Routes live in do_GET.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /the prompt it was given/i }));
    expect(screen.getByText("Survey the webapp conventions.")).toBeTruthy();
  });

  it("shows a builder's patch", () => {
    render(<AgentMapHost />);
    act(() => useAgentMap.getState().show("c1"));
    fireEvent.click(screen.getByRole("button", { name: /api-route/ }));
    expect(screen.getByText("/p/api-route.patch")).toBeTruthy();
  });

  it("offers Stop while the run is going, addressed to that run", () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => new Response(JSON.stringify({ stopping: true }), { status: 200 }));
    render(<AgentMapHost />);
    act(() => useAgentMap.getState().show("c1"));
    fireEvent.click(screen.getByRole("button", { name: /stop workflow/i }));
    expect(fetch).toHaveBeenCalledWith("/api/workflows/wf-1/stop", { method: "POST" });
  });

  it("closes when asked", () => {
    render(<AgentMapHost />);
    act(() => useAgentMap.getState().show("c1"));
    expect(screen.getByText("Agent map")).toBeTruthy();
    act(() => useAgentMap.getState().close());
    expect(screen.queryByText("Agent map")).toBeNull();
  });
});
