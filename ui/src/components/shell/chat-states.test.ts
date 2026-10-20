import { describe, expect, it } from "vitest";

import type { ConversationSummary } from "@/lib/backend";

import {
  agentsWorking,
  chatState,
  clock,
  permissionHolder,
  presenceFor,
  type ChatFacts,
} from "./chat-states";

function facts(over: Partial<ChatFacts> = {}): ChatFacts {
  return { live: new Set(), waiting: new Set(), asking: null, agents: new Map(), ...over };
}

function chat(id: string, agents = 0, working = 0): ConversationSummary {
  return {
    id,
    title: id,
    sessionId: "",
    createdAt: "",
    updatedAt: "",
    messages: 1,
    lastSaid: "",
    transcript: "",
    projectId: null,
    working: false,
    waiting: false,
    parentId: null,
    agentName: "",
    agentState: "",
    agents,
    agentsWorking: working,
  };
}

describe("which chats have agents working", () => {
  it("takes the listing's own count, and only where something is busy", () => {
    const got = agentsWorking([chat("c1", 3, 2), chat("c2", 2, 0), chat("c3")]);
    expect([...got.entries()]).toEqual([["c1", 2]]);
  });
});

describe("whose permission request it is", () => {
  it("is the one live chat not already waiting on a question", () => {
    expect(permissionHolder(1, ["a", "b"], new Set(["a"]))).toBe("b");
  });

  it("is nobody's when two parked turns could have asked", () => {
    expect(permissionHolder(1, ["a", "b"], new Set())).toBeNull();
  });

  it("is nobody's when nothing is pending", () => {
    expect(permissionHolder(0, ["a"], new Set())).toBeNull();
  });
});

describe("a chat's state", () => {
  const busy = new Map([["c1", 2]]);

  it("ranks needing you over working over agents working under it", () => {
    expect(chatState("c1", facts({ waiting: new Set(["c1"]), live: new Set(["c1"]), agents: busy }))?.tag).toBe("Asking");
    expect(chatState("c1", facts({ asking: "c1", live: new Set(["c1"]) }))?.tag).toBe("Needs yes");
    expect(chatState("c1", facts({ live: new Set(["c1"]), agents: busy }))?.tone).toBe("working");
    expect(chatState("c1", facts({ agents: busy }))).toMatchObject({ tone: "agents", tag: "2 agents" });
  });

  it("has nothing to say about a draft or an idle chat", () => {
    expect(chatState("", facts({ live: new Set([""]) }))).toBeUndefined();
    expect(chatState("c9", facts())).toBeUndefined();
  });
});

describe("the presence pill", () => {
  it("speaks for the chat in front of you first", () => {
    expect(presenceFor("c1", facts({ waiting: new Set(["c1", "c2"]) }), undefined)).toEqual({
      kind: "waiting",
      chats: 2,
      target: "",
    });
    expect(presenceFor("c1", facts({ live: new Set(["c1"]), waiting: new Set(["c2"]) }), 5)).toEqual({
      kind: "working",
      since: 5,
    });
  });

  it("counts the agents under the chat in front of you, reached through its root from an agent", () => {
    const busy = new Map([["c1", 2]]);
    expect(presenceFor("c1", facts({ agents: busy }), undefined)).toEqual({
      kind: "agents",
      count: 2,
      root: "c1",
      target: "",
    });
    // Reading one of c1's agents: the listing does not hold it, so its root speaks for it.
    expect(presenceFor("agent-7", facts({ agents: busy }), undefined, "c1")).toEqual({
      kind: "agents",
      count: 2,
      root: "c1",
      target: "",
    });
  });

  it("counts agents elsewhere ahead of a question elsewhere, and points at where they work", () => {
    const busy = new Map([
      ["c2", 1],
      ["c4", 2],
    ]);
    expect(presenceFor("c1", facts({ agents: busy, waiting: new Set(["c3"]) }), undefined)).toEqual({
      kind: "agents",
      count: 3,
      root: "c2",
      target: "c2",
    });
  });

  it("lets the chat's own live turn speak before its agents", () => {
    expect(presenceFor("c1", facts({ live: new Set(["c1"]), agents: new Map([["c1", 3]]) }), 9)).toEqual({
      kind: "working",
      since: 9,
    });
  });

  it("says he is waiting on you when a chat elsewhere is asking, and takes you there", () => {
    expect(presenceFor("c1", facts({ waiting: new Set(["c3"]) }), undefined)).toEqual({
      kind: "waiting",
      chats: 1,
      target: "c3",
    });
  });

  it("is simply here otherwise", () => {
    expect(presenceFor("c1", facts(), undefined)).toEqual({ kind: "here" });
  });
});

describe("the clock", () => {
  it("reads like a stopwatch", () => {
    expect(clock(68_000)).toBe("1:08");
    expect(clock(4_000)).toBe("0:04");
    expect(clock(3_723_000)).toBe("1:02:03");
    expect(clock(-5)).toBe("0:00");
  });
});
