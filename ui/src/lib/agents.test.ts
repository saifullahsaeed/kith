/**
 * The words and shapes every agent surface shares — the sidebar fold, the title bar's dialog, the
 * banner on an agent's page and the labels on mail in a thread. Pinned here because three surfaces
 * read them, and a disagreement between those is the kind of bug nobody notices until two of them
 * say different things about the same agent.
 */
import { describe, expect, it } from "vitest";

import type { AgentNode } from "@/lib/backend";
import { toThreadMessages } from "@/components/chat/to-thread-messages";

import {
  SENT_BY,
  agentLook,
  countAgents,
  flattenAgents,
  senderLabel,
  sentBy,
  withoutHeading,
} from "./agents";

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

const TREE = [
  agent("api", { state: "waiting" }, [
    agent("schema", { state: "working", working: true }),
    agent("routes", { state: "working", asking: true }),
  ]),
  agent("old", { state: "closed" }, [agent("old-child", { state: "closed" })]),
  agent("web", { state: "stopped" }),
];

describe("a tree as rows", () => {
  it("puts each agent before its own, a level deeper", () => {
    expect(flattenAgents(TREE).map(({ node, depth }) => `${depth}:${node.name}`)).toEqual([
      "0:api",
      "1:schema",
      "1:routes",
      "0:web",
    ]);
  });

  it("leaves closed agents out with everything under them, unless asked", () => {
    expect(flattenAgents(TREE).some(({ node }) => node.name.startsWith("old"))).toBe(false);
    expect(flattenAgents(TREE, true).map(({ node }) => node.name)).toContain("old-child");
  });

  it("counts a turn running and a question pending as working, and closed apart", () => {
    expect(countAgents(TREE)).toEqual({ open: 4, working: 2, closed: 2 });
  });
});

describe("what an agent is doing", () => {
  it("reads a live turn over whatever the state column says", () => {
    expect(agentLook({ state: "done", working: true, asking: false })).toBe("live");
    expect(agentLook({ state: "working", working: false, asking: true })).toBe("asking");
    // Started, its first turn not yet begun: still live rather than blinking idle for a beat.
    expect(agentLook({ state: "working", working: false, asking: false })).toBe("live");
    expect(agentLook({ state: "closed", working: true, asking: false })).toBe("closed");
    expect(agentLook({ state: "", working: false, asking: false })).toBe("idle");
  });
});

describe("who sent a message", () => {
  it("says it for a reader, with the name apart so it can be a link", () => {
    expect(senderLabel({ id: "a", name: "api", kind: "report" })).toEqual({
      before: "Report from ",
      name: "api",
      after: "",
    });
    expect(senderLabel({ id: "a", name: "api", kind: "question" })).toEqual({
      before: "",
      name: "api",
      after: " is asking",
    });
    expect(senderLabel({ id: "c", name: "conductor", kind: "task" }).before).toBe("Task from ");
    expect(senderLabel({ id: "", name: "Kith", kind: "resume" })).toEqual({
      before: "Kith restarted",
      name: "",
      after: "",
    });
  });

  it("takes the server's heading off, and only that", () => {
    expect(withoutHeading("[Report from your agent “api”]\n\nAll green.\n\nDetails.")).toBe(
      "All green.\n\nDetails.",
    );
    expect(withoutHeading("[Your task, from conductor]")).toBe("");
    // Not a heading: a link on the first line, or a bracket that is not the whole line.
    expect(withoutHeading("[the docs](https://x.dev) say so")).toBe("[the docs](https://x.dev) say so");
    expect(withoutHeading("plain words")).toBe("plain words");
  });
});

describe("mail in a stored conversation", () => {
  it("carries its sender into the message, and leaves the heading behind", () => {
    const [task, report, mine] = toThreadMessages([
      {
        role: "user",
        parts: [{ kind: "text", text: "[Your task, from conductor]\n\nBuild the API." }],
        from: { id: "c1", name: "conductor", kind: "task" },
      },
      {
        role: "system",
        parts: [{ kind: "text", text: "[Report from your agent “web”]\n\nShipped." }],
        from: { id: "a2", name: "web", kind: "report" },
      },
      { role: "user", parts: [{ kind: "text", text: "[not a heading] really" }] },
    ]);

    expect(task?.content).toEqual([{ type: "text", text: "Build the API." }]);
    expect(sentBy(task?.metadata?.custom)).toEqual({ id: "c1", name: "conductor", kind: "task" });
    expect(report?.content).toEqual([{ type: "text", text: "Shipped." }]);
    expect(sentBy(report?.metadata?.custom)?.kind).toBe("report");
    // The person's own message: untouched, and nobody else's.
    expect(mine?.content).toEqual([{ type: "text", text: "[not a heading] really" }]);
    expect(mine?.metadata?.custom?.[SENT_BY]).toBeUndefined();
  });
});
