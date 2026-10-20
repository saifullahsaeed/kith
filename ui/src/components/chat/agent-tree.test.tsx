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
    tokens: 0,
    costUsd: 0,
    seconds: 0,
    contextTokens: 0,
    modelUsed: "",
    task: "",
    agents,
    ...over,
  };
}

const TREE: AgentTree = {
  self: {
    id: "c1",
    title: "Build the payroll module",
    name: "conductor",
    working: false,
    tokens: 900_000,
    costUsd: 1.2,
    seconds: 400,
    contextTokens: 522_600,
    modelUsed: "anthropic/claude-opus-5.5",
  },
  root: "c1",
  chain: [{ id: "c1", name: "conductor", state: "" }],
  agents: [
    agent("api", { state: "waiting", branch: "kith/api", lastSaid: "Waiting on schema." }, [
      agent("schema", {
        state: "working",
        working: true,
        lastSaid: "Writing the migration.",
        tokens: 446_512,
        createdAt: new Date(Date.now() - (23 * 60 + 26) * 1000).toISOString(),
        task: "Write the payroll migration.",
      }),
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

describe("the agent map", () => {
  function open(onOpen = vi.fn()) {
    render(
      <Dialog open>
        <AgentTreeDialog root="c1" onOpen={onOpen} />
      </Dialog>,
    );
    return onOpen;
  }

  it("grows from the chat, with its model and the context it carries", async () => {
    server();
    open();

    expect(await screen.findByText("Build the payroll module")).toBeInTheDocument();
    expect(screen.getByText("claude-opus-5.5 · 522.6k in context")).toBeInTheDocument();
    expect(screen.getByText(/2 agents · 1 working · 1 closed · click an agent for details/)).toBeInTheDocument();
  });

  it("nests each agent's agents beside it, with how long and how many tokens", async () => {
    server();
    open();

    const api = await screen.findByRole("button", { name: /^api/ });
    const schema = screen.getByRole("button", { name: /^schema/ });
    // schema hangs from api: its column is inside api's row of the tree.
    expect(api.closest("li")).toContainElement(schema);
    expect(within(schema).getByText(/^23m 2[6-9]s · 446\.5k tokens$/)).toBeInTheDocument();
  });

  it("keeps a closed agent behind a toggle", async () => {
    server();
    open();

    await screen.findByRole("button", { name: /^api/ });
    expect(screen.queryByText("spike")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show 1 closed" }));
    expect(screen.getByText("spike")).toBeInTheDocument();
  });

  it("shows an agent's details on a click, and opens its conversation from there", async () => {
    server();
    const onOpen = open();

    fireEvent.click(await screen.findByRole("button", { name: /^schema/ }));
    const details = screen.getByRole("complementary", { name: "schema, in detail" });
    expect(within(details).getByText("Writing the migration.")).toBeInTheDocument();
    fireEvent.click(within(details).getByRole("button", { name: "The task it was given" }));
    expect(within(details).getByText("Write the payroll migration.")).toBeInTheDocument();
    fireEvent.click(within(details).getByRole("button", { name: /Open its conversation/ }));
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

    await screen.findByRole("button", { name: /^api/ });
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
