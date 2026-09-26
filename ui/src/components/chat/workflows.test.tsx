/**
 * A workflow's progress arrives as a flat event log, and the panel shows it as runs with their
 * agents under them.
 *
 * The fold between those two shapes is the whole of this component's logic, and it is the kind of
 * thing that looks obviously right while silently dropping the last step, merging two agents into
 * one, or hanging an agent under the wrong run. It matters more here than elsewhere: this is the
 * *only* place a workflow's agents are ever shown. Their greps and reads are discarded by design,
 * and the run outlives the turn that started it, so a fold that loses them loses them for good.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fireEvent, render, screen } from "@/test/render";
import { foldAgents, foldRuns, isWorkflowLine, mergeRuns, runsFromBoard, Workflows } from "./workflows";
import type { ActivityItem } from "@/lib/backend/activity";

const at = "2026-09-25T10:00:00Z";

function run(
  id: string,
  state: NonNullable<ActivityItem["workflow"]>["state"],
  text: string,
  extra: Partial<NonNullable<ActivityItem["workflow"]>> = {},
  conversation = "conv-1",
): ActivityItem {
  return { kind: "workflow", text, at, conversation, workflow: { run: id, state, name: "port-rag", ...extra } };
}

function agent(
  id: string,
  state: "running" | "step" | "done",
  text: string,
  extra: Partial<ActivityItem> & { objective?: string; role?: string; runId?: string } = {},
): ActivityItem {
  const { objective, role, runId, ...rest } = extra;
  return {
    kind: "errand",
    text,
    at,
    conversation: "conv-1",
    errand: { id, state, objective: objective ?? `look at ${id}`, role: role ?? "scout", run: runId ?? "wf-1" },
    ...rest,
  };
}

beforeEach(() => {
  // The server's board says nothing unless a test says otherwise; the feed carries the run.
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({ runs: [] })));
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("folding the feed into agents", () => {
  it("keeps interleaved agents apart", () => {
    const folded = foldAgents(
      [
        agent("a", "running", "a"),
        agent("b", "running", "b"),
        agent("a", "step", "grep", { tool: "grep", args: { pattern: "fold" } }),
        agent("b", "step", "read_file", { tool: "read_file", args: { path: "b.py" } }),
        agent("a", "step", "read_file", { tool: "read_file", args: { path: "a.py" } }),
        agent("b", "done", "read_file"),
      ],
      "conv-1",
    );
    expect(folded.map((one) => one.id)).toEqual(["a", "b"]);
    expect(folded[0].steps.map((s) => s.tool)).toEqual(["grep", "read_file"]);
    expect(folded[0].running).toBe(true);
    expect(folded[1].running).toBe(false);
    expect(folded[1].tally).toBe("read_file");
  });
});

describe("folding the feed into runs", () => {
  it("carries the plan, the phase it is in, and how it ended", () => {
    const [one] = foldRuns(
      [
        run("wf-1", "started", "port-rag", { description: "port it", phases: ["Survey", "Port"] }),
        run("wf-1", "phase", "Port"),
        run("wf-1", "log", "3 packages to port"),
        run("wf-1", "finished", "The workflow `port-rag` (wf-1) finished after 2m10s"),
      ],
      "conv-1",
    );
    expect(one.phases).toEqual(["Survey", "Port"]);
    expect(one.phase).toBe("Port");
    expect(one.state).toBe("finished");
    expect(one.said).toContain("finished after 2m10s");
  });

  it("hangs each agent under the run that sent it", () => {
    const runs = foldRuns(
      [
        run("wf-1", "started", "one"),
        run("wf-2", "started", "two"),
        agent("a", "running", "a", { runId: "wf-1" }),
        agent("b", "running", "b", { runId: "wf-2" }),
      ],
      "conv-1",
    );
    expect(runs.map((r) => r.agents.map((x) => x.id))).toEqual([["a"], ["b"]]);
  });

  it("ignores another conversation's runs", () => {
    const runs = foldRuns([run("wf-1", "started", "x"), run("wf-9", "started", "y", {}, "conv-2")], "conv-1");
    expect(runs.map((r) => r.id)).toEqual(["wf-1"]);
  });

  it("does not count as one of the turn's rounds", () => {
    // Ninety agent tool calls are not ninety rounds of the conversation.
    expect(isWorkflowLine(agent("a", "step", "grep"))).toBe(true);
    expect(isWorkflowLine(run("wf-1", "phase", "Port"))).toBe(true);
    expect(isWorkflowLine({ kind: "tool", text: "grep", at })).toBe(false);
  });
});

describe("the section itself", () => {
  it("renders nothing when no workflow has run", () => {
    const { container } = render(<Workflows lines={[]} conversationId="conv-1" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("still draws a run the feed has forgotten, from the server's board", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      async () =>
        new Response(
          JSON.stringify({
            runs: [
              {
                run: "wf-9",
                name: "remembered",
                phases: ["Build"],
                phase: "Build",
                state: "running",
                started: 1,
                agents: [{ id: "a", label: "discover-module", kind: "builder", phase: "Build", state: "running", started: 2 }],
              },
            ],
          }),
        ),
    );
    render(<Workflows lines={[]} conversationId="conv-1" />);
    expect(await screen.findByText("remembered")).toBeTruthy();
    expect(screen.getByText("discover-module")).toBeTruthy();
  });

  it("shows a running agent's steps in English, not as function calls", () => {
    render(
      <Workflows
        lines={[
          run("wf-1", "started", "port-rag", { phases: ["Survey"] }),
          agent("a", "running", "x", { objective: "where the fold is triggered from" }),
          agent("a", "step", "grep", {
            objective: "where the fold is triggered from",
            tool: "grep",
            args: { pattern: "_FOLD_ABOVE_SHARE" },
          }),
        ]}
        conversationId="conv-1"
      />,
    );
    expect(screen.getByText("port-rag")).toBeTruthy();
    expect(screen.getByText("where the fold is triggered from")).toBeTruthy();
    // Capitalised, and in the present tense on the step it is on now ("Searching files for") —
    // either way a sentence, never `grep`.
    expect(screen.getByText(/search(ed|ing) files for/i)).toBeTruthy();
    expect(screen.getByText(/_FOLD_ABOVE_SHARE/)).toBeTruthy();
  });

  it("drops a finished agent's steps and keeps its tally", () => {
    render(
      <Workflows
        lines={[
          run("wf-1", "started", "port-rag"),
          agent("a", "running", "x"),
          agent("a", "step", "grep", { tool: "grep", args: { pattern: "fold" } }),
          agent("a", "done", "grep x3, read_file x8"),
        ]}
        conversationId="conv-1"
      />,
    );
    expect(screen.queryByText(/search(ed|ing) files for/i)).toBeNull();
    expect(screen.getByText("grep x3, read_file x8")).toBeTruthy();
  });

  it("offers Stop while running, and it asks the server to stop that run", () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => new Response(JSON.stringify({ stopping: true }), { status: 200 }));
    render(<Workflows lines={[run("wf-1", "started", "port-rag")]} conversationId="conv-1" />);
    fireEvent.click(screen.getByRole("button", { name: /stop/i }));
    expect(fetch).toHaveBeenCalledWith("/api/workflows/wf-1/stop", { method: "POST" });
  });

  it("has no Stop once the run has ended", () => {
    render(
      <Workflows
        lines={[run("wf-1", "started", "port-rag"), run("wf-1", "stopped", "The workflow was stopped")]}
        conversationId="conv-1"
      />,
    );
    expect(screen.queryByRole("button", { name: /stop/i })).toBeNull();
    // Said by the end mark, named — the word beside it made way for the map button.
    expect(screen.getByRole("img", { name: "stopped" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /agent map/i })).toBeTruthy();
  });

  it("says which agents are builders, in words and not only in colour", () => {
    render(
      <Workflows
        lines={[
          run("wf-1", "started", "port-rag"),
          agent("w1", "running", "x", { objective: "rewrite store.py", role: "builder" }),
          agent("w2", "running", "y", { objective: "where does it live", role: "scout" }),
        ]}
        conversationId="conv-1"
      />,
    );
    // One label, not two: a scout is the unmarked case, so only the one editing files is named.
    expect(screen.getAllByText("builder")).toHaveLength(1);
  });

  it("folds a long running prompt, and opens it on click", () => {
    const long = "change every call site. ".repeat(40);
    render(
      <Workflows
        lines={[run("wf-1", "started", "port-rag"), agent("w1", "running", long, { objective: long, role: "builder" })]}
        conversationId="conv-1"
      />,
    );
    const head = screen.getByRole("button", { expanded: false });
    expect(head.className).toContain("line-clamp-2");
    fireEvent.click(head);
    expect(screen.getByRole("button", { expanded: true }).className).not.toContain("line-clamp-2");
  });
});

describe("the board and the feed together", () => {
  it("keeps the board's record and lays the feed's newer steps over it", () => {
    const board = runsFromBoard([
      {
        run: "wf-1",
        name: "port-rag",
        state: "running",
        started: 1,
        agents: [{ id: "a", label: "x", state: "running", started: 2, steps: [{ tool: "grep", args: {} }] }],
      },
    ]);
    const live = foldRuns(
      [
        run("wf-1", "started", "port-rag"),
        agent("a", "running", "x"),
        agent("a", "step", "grep", { tool: "grep" }),
        agent("a", "step", "read_file", { tool: "read_file" }),
        agent("b", "running", "y"),
      ],
      "conv-1",
    );
    const [merged] = mergeRuns(board, live);
    expect(merged.agents.map((one) => one.id)).toEqual(["a", "b"]);
    expect(merged.agents[0].steps.map((one) => one.tool)).toEqual(["grep", "read_file"]);
  });
});
