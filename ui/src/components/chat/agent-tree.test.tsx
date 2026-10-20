/**
 * The two places a chat's agents are listed: the title bar's dialog and the fold under its row in
 * the sidebar. Both read `GET /api/conversations/<id>/agents`, both hide closed agents until asked,
 * and both open an agent the way a chat row opens a chat — so both are held to that here, against
 * the server's own shape.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { Dialog } from "@/components/ui/dialog";

import { fireEvent, render, screen, waitFor, within } from "@/test/render";
import type { AgentNode, AgentTree, ConversationSummary } from "@/lib/backend";

import { AgentTreeDialog } from "./agent-tree";
import { AgentsUnder } from "./history-panel";

function agent(name: string, over: Partial<AgentNode> = {}, agents: AgentNode[] = []): AgentNode {
  return {
    id: `id-${name}`,
    name,
    state: "done",
    working: false,
    asking: false,
    model: "",
    branch: "",
    worktree: "",
    folder: "",
    lastSaid: "",
    createdAt: "",
    updatedAt: "",
    agents,
    ...over,
  };
}

const TREE: AgentTree = {
  root: "c1",
  chain: [{ id: "c1", name: "conductor", state: "" }],
  agents: [
    agent("api", { state: "waiting", branch: "kith/api", lastSaid: "Waiting on schema." }, [
      agent("schema", { state: "working", working: true, lastSaid: "Writing the migration." }),
    ]),
    agent("spike", { state: "closed", lastSaid: "Merged." }),
  ],
};

/** Answers the tree, records every request, and says yes to a stop. */
function server(tree: AgentTree = TREE) {
  const calls: { url: string; method: string }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, method: init?.method ?? "GET" });
    const body = url.endsWith("/agents/stop") ? { stopped: ["api"] } : tree;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  return calls;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the agents dialog", () => {
  function open(onOpen = vi.fn()) {
    render(
      <Dialog open>
        <AgentTreeDialog root="c1" onOpen={onOpen} />
      </Dialog>,
    );
    return onOpen;
  }

  it("draws the tree nested, with each agent's state, branch and last word", async () => {
    server();
    open();

    const list = await screen.findByRole("list", { name: "Agents" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent(/^api/);
    expect(rows[1]).toHaveTextContent(/^schema/);
    // A level deeper is a step further in.
    expect(rows[1]!.style.paddingInlineStart).not.toBe(rows[0]!.style.paddingInlineStart);
    expect(within(rows[0]!).getByText("kith/api")).toBeInTheDocument();
    expect(within(rows[0]!).getByText("waiting on its own agents")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("Writing the migration.")).toBeInTheDocument();
    expect(screen.getByText(/2 agents open · 1 working · 1 closed/)).toBeInTheDocument();
  });

  it("keeps a closed agent behind a toggle", async () => {
    server();
    open();

    await screen.findByText("api");
    expect(screen.queryByText("spike")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show 1 closed" }));
    expect(screen.getByText("spike")).toBeInTheDocument();
  });

  it("opens an agent's conversation", async () => {
    server();
    const onOpen = open();

    fireEvent.click(await screen.findByRole("button", { name: "Open schema" }));
    expect(onOpen).toHaveBeenCalledWith("id-schema");
  });

  it("stops every agent under the chat, and only while one is working", async () => {
    const calls = server();
    open();

    const stop = await screen.findByRole("button", { name: "Stop all agents" });
    await waitFor(() => expect(stop).toBeEnabled());
    fireEvent.click(stop);
    await waitFor(() =>
      expect(calls).toContainEqual({ url: "/api/conversations/c1/agents/stop", method: "POST" }),
    );
  });

  it("offers no stop when nothing is running", async () => {
    server({ ...TREE, agents: [agent("api", { state: "done" })] });
    open();

    await screen.findByText("api");
    expect(screen.getByRole("button", { name: "Stop all agents" })).toBeDisabled();
  });
});

describe("the fold under a chat in the sidebar", () => {
  const chat: ConversationSummary = {
    id: "c1",
    title: "build the thing",
    sessionId: "",
    createdAt: "",
    updatedAt: "",
    messages: 3,
    lastSaid: "",
    transcript: "",
    projectId: null,
    working: false,
    waiting: false,
    parentId: null,
    agentName: "",
    agentState: "",
    agents: 3,
    agentsWorking: 2,
  };

  it("says how many, and how many are working, without fetching the tree", () => {
    const calls = server();
    render(<AgentsUnder item={chat} indent={8} open={false} onToggle={() => {}} activeId="" onOpen={() => {}} />);

    expect(screen.getByRole("button", { expanded: false })).toHaveTextContent("3 agents · 2 working");
    expect(calls).toEqual([]);
  });

  it("lists the tree when open, marks the one you are reading, and opens one on click", async () => {
    server();
    const onOpen = vi.fn();
    render(
      <AgentsUnder item={chat} indent={8} open onToggle={() => {}} activeId="id-schema" onOpen={onOpen} />,
    );

    const list = await screen.findByRole("list", { name: "Its agents" });
    const schema = within(list).getByRole("button", { name: /^schema/ });
    expect(schema).toHaveAttribute("aria-current", "page");
    expect(within(list).queryByText("spike")).toBeNull();

    fireEvent.click(within(list).getByRole("button", { name: /^api/ }));
    expect(onOpen).toHaveBeenCalledWith("id-api");
  });
});
