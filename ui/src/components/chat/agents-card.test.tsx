/**
 * The Activity tab's agents card: counted, not drawn.
 *
 * It was the whole agent map, compact, and the person asked for it to stop being a list — the
 * sidebar has the list and the title bar has the map. So the card is held to saying the four things
 * a glance asks (what state, who is asking, what it cost, for how long) and to being the way into
 * the map, and to drawing no agent rows of its own.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fireEvent, render, screen } from "@/test/render";
import type { AgentNode, AgentTree } from "@/lib/backend";
import { useAgentTree } from "@/lib/agents";

import { AgentsCard } from "./agents-card";

const MINUTE = 60_000;
const ago = (ms: number) => new Date(Date.now() - ms).toISOString();

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
    createdAt: ago(30 * MINUTE),
    updatedAt: ago(5 * MINUTE),
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

/** c1 → api (waiting on schema) → schema (working) and docs (asking); spike done; old closed. */
const TREE: AgentTree = {
  self: null,
  root: "c1",
  chain: [{ id: "c1", name: "conductor", state: "" }],
  agents: [
    agent("api", { state: "waiting", tokens: 100_000, createdAt: ago(42 * MINUTE) }, [
      agent("schema", { state: "working", working: true, tokens: 200_000 }),
      agent("docs", { state: "working", asking: true, tokens: 12_400 }),
    ]),
    agent("spike", { tokens: 1_000 }),
    agent("old", { state: "closed" }),
  ],
};

/** Answers every tree question with `tree`, rooted at c1 whichever conversation was asked about. */
function server(tree: AgentTree = TREE) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify(tree), { status: 200, headers: { "Content-Type": "application/json" } }),
  );
}

beforeEach(() => {
  useAgentTree.setState({ root: "" });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the agents card", () => {
  it("counts the whole tree by state, closed only because there is one", async () => {
    server();
    render(<AgentsCard conversationId="id-schema" />);

    const card = await screen.findByRole("button", { name: /Open agent map/ });
    expect(card).toHaveTextContent("Agents");
    expect(card).toHaveTextContent("1 working");
    expect(card).toHaveTextContent("1 waiting on agents");
    expect(card).toHaveTextContent("1 asking");
    expect(card).toHaveTextContent("1 done");
    expect(card).toHaveTextContent("1 closed");
    expect(card).not.toHaveTextContent("stopped");
  });

  it("says what the tree has spent and how long it has been running", async () => {
    server();
    render(<AgentsCard conversationId="c1" />);

    // 100k + 200k + 12.4k + 1k, and from api's start 42 minutes ago — still running, so to now.
    expect(await screen.findByText(/^313\.4k tokens · running 42m \d+s$/)).toBeInTheDocument();
  });

  it("names whoever is asking its handler, on a line of its own", async () => {
    server();
    render(<AgentsCard conversationId="c1" />);

    expect(await screen.findByText("docs is asking its handler")).toBeInTheDocument();
  });

  it("leaves out the zero ends and the asking line when nobody is there", async () => {
    server({ ...TREE, agents: [agent("spike"), agent("api", { state: "working", working: true })] });
    render(<AgentsCard conversationId="c1" />);

    const card = await screen.findByRole("button", { name: /Open agent map/ });
    expect(card).toHaveTextContent("1 working");
    expect(card).not.toHaveTextContent("asking");
    expect(card).not.toHaveTextContent("closed");
    expect(screen.queryByText(/is asking its handler/)).toBeNull();
  });

  it("lists no agents — it is a summary, and the map is a click away", async () => {
    server();
    render(<AgentsCard conversationId="c1" />);

    const card = await screen.findByRole("button", { name: /Open agent map/ });
    expect(card).not.toHaveTextContent("schema");
    expect(card).not.toHaveTextContent("spike");
    fireEvent.click(card);
    expect(useAgentTree.getState().root).toBe("c1");
  });

  it("renders nothing for a chat with no agents", async () => {
    server({ ...TREE, agents: [] });
    const { container, client } = render(<AgentsCard conversationId="c1" />);

    await vi.waitFor(() => expect(client.getQueryData(["agentTree", "c1"])).toBeDefined());
    expect(container).toBeEmptyDOMElement();
  });
});
