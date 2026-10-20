/**
 * An agent's own conversation, opened in a pane.
 *
 * Two promises, both made by the server and both easy for the interface to break. The person
 * cannot type to an agent — `POST /api/chat` and `/steer` answer 409 — so the pane must not offer
 * a composer, an edit pencil or a retry, each of which would only ever be refused. And nothing in
 * an agent's thread was written by the person: its task and everything its handler says come in
 * as `user` turns, mail from its own agents as `system` ones, and every one of them carries who
 * sent it. A thread that drew those as yours would be the app putting words in your mouth.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fireEvent, render, screen, waitFor, within } from "@/test/render";
import { ConfirmProvider } from "@/components/ui/confirm";
import { useLayout } from "@/components/shell/layout/store";
import { pane } from "@/components/shell/layout/tree";
import { OpenConversation } from "@/lib/open-conversation";
import { ChatPane } from "./chat-pane";

/** The agent `api`, started by the agent `backend`, started by the person's chat. `asChat` is the
 *  same transcript as a chat of the person's own, for the control half of a comparison. */
function detail({ asChat = false, endsWithPlain = false } = {}) {
  return {
    id: "a1",
    title: "api",
    sessionId: "s1",
    createdAt: "",
    updatedAt: "",
    messageCount: 3,
    lastSaid: "",
    transcript: "",
    projectId: null,
    working: false,
    waiting: false,
    parentId: asChat ? null : "h1",
    agentName: asChat ? "" : "api",
    agentState: asChat ? "" : "working",
    agents: 0,
    agentsWorking: 0,
    turnCount: 3,
    windowStart: 0,
    hasMore: false,
    timeline: [
      {
        role: "user",
        parts: [{ kind: "text", text: "[Your task, from backend]\n\nBuild the API." }],
        at: "",
        from: { id: "h1", name: "backend", kind: "task" },
      },
      { role: "assistant", parts: [{ kind: "text", text: "Starting on the routes." }], at: "" },
      {
        role: "system",
        parts: [{ kind: "text", text: "[Report from your agent “db”]\n\nMigrated." }],
        at: "",
        from: { id: "d1", name: "db", kind: "report" },
      },
      // A message with no sender at the very end, where the edit pencil is shown — so whether it
      // is offered is decided by whose conversation this is, and nothing else.
      ...(endsWithPlain ? [{ role: "user", parts: [{ kind: "text", text: "plain words" }], at: "" }] : []),
    ],
  };
}

const TREE = {
  root: "c1",
  agents: [],
  chain: [
    { id: "c1", name: "conductor", state: "" },
    { id: "h1", name: "backend", state: "waiting" },
    { id: "a1", name: "api", state: "working" },
  ],
};

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });

/** Everything the pane asks for. `live` says whether the agent has a turn running right now. */
function server({ live = false, asChat = false, endsWithPlain = false } = {}) {
  const calls: { url: string; method: string }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, method: init?.method ?? "GET" });
    if (url.includes("/attach")) return new Response(null, { status: 204 });
    if (url.includes("/checkpoints")) return json({ checkpoints: [] });
    if (url.endsWith("/api/chat/live")) return json({ conversations: live ? ["a1"] : [] });
    if (url.endsWith("/api/chat/a1/stop")) return json({ stopping: true });
    if (url.endsWith("/api/conversations/a1/agents")) return json(TREE);
    if (url.includes("/api/conversations/a1")) return json(detail({ asChat, endsWithPlain }));
    return json({});
  });
  return calls;
}

function open() {
  const opened = vi.fn();
  render(
    <ConfirmProvider>
      <OpenConversation.Provider value={opened}>
        <ChatPane conversationId="a1" uid="tab-agent" />
      </OpenConversation.Provider>
    </ConfirmProvider>,
  );
  return opened;
}

beforeEach(() => {
  useLayout.setState({ tree: pane([{ surface: "chat", conversationId: "a1" }]), focused: "" });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("an agent's conversation", () => {
  it("has no composer — a banner says whose it is instead", async () => {
    server();
    open();

    const banner = await screen.findByRole("note", { name: "This conversation is an agent's" });
    await waitFor(() =>
      expect(banner).toHaveTextContent("api is an agent of backend — you talk to the conductor."),
    );
    expect(screen.queryByRole("button", { name: "Send message" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Retry this turn" })).toBeNull();
  });

  it("offers no edit pencil even on a message with no sender — while a chat of yours does", async () => {
    server({ endsWithPlain: true });
    open();
    await screen.findByText("plain words");
    await screen.findByRole("note", { name: "This conversation is an agent's" });
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  });

  it("is the person's own chat, with its pencil, when the record says it has no handler", async () => {
    server({ asChat: true, endsWithPlain: true });
    open();
    await screen.findByText("plain words");
    expect(await screen.findByRole("button", { name: "Edit" })).toBeInTheDocument();
    expect(screen.queryByRole("note", { name: "This conversation is an agent's" })).toBeNull();
  });

  it("walks the chain from the conductor down, each link a way into that conversation", async () => {
    server();
    const opened = open();

    const chain = await screen.findByRole("navigation", { name: "Who it works for" });
    await waitFor(() => expect(within(chain).getByText("conductor")).toBeInTheDocument());
    expect(within(chain).getByText("api")).toHaveAttribute("aria-current", "page");
    fireEvent.click(within(chain).getByRole("button", { name: "conductor" }));
    expect(opened).toHaveBeenCalledWith("c1");
  });

  it("labels its task with who sent it, not as something you said", async () => {
    server();
    const opened = open();

    await screen.findByText("Build the API.");
    const task = document.querySelector<HTMLElement>('[data-role="user"][data-from="task"]');
    expect(task).not.toBeNull();
    expect(task!).toHaveTextContent("Task from backend");
    // The server's heading was for the model; the label says it now.
    expect(task!).not.toHaveTextContent("[Your task");
    fireEvent.click(within(task!).getByRole("button", { name: "backend" }));
    expect(opened).toHaveBeenCalledWith("h1");
  });

  it("draws an agent's report as a card from that agent", async () => {
    server();
    open();

    await screen.findByText("Migrated.");
    const report = document.querySelector<HTMLElement>('[data-from="report"]');
    expect(report).not.toBeNull();
    expect(report!).toHaveTextContent("Report from db");
    expect(report!).not.toHaveTextContent("Report from your agent");
    expect(report!).not.toHaveTextContent("Woken");
  });

  it("offers Stop only while a turn is running, and stops that agent's turn", async () => {
    const calls = server({ live: true });
    open();

    const stop = await screen.findByRole("button", { name: "Stop api" });
    fireEvent.click(stop);
    await waitFor(() => expect(calls).toContainEqual({ url: "/api/chat/a1/stop", method: "POST" }));
  });

  it("offers no Stop while it is idle", async () => {
    server();
    open();

    await screen.findByRole("note", { name: "This conversation is an agent's" });
    await screen.findByText("Build the API.");
    expect(screen.queryByRole("button", { name: "Stop api" })).toBeNull();
  });
});
