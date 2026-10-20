/**
 * Agents opened inside their conductor's tab: the strip's own bookkeeping, and the open that puts
 * an agent there instead of in a tab of its own.
 *
 * The second half is what the person asked for — "opening an agent must not open a new top-level
 * tab" — so it is held against the real layout store: whatever the tree says is what the tab bar
 * draws.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useLayout } from "@/components/shell/layout/store";
import { panes, tabKey } from "@/components/shell/layout/tree";
import type { AgentTree, ConversationSummary } from "@/lib/backend";
import { keys } from "@/lib/query-keys";
import { testQueryClient } from "@/test/render";

import {
  conductorOf,
  knownConductor,
  openInConductor,
  readViews,
  supersedeOpens,
  useAgentViews,
} from "./agent-views";

const views = () => useAgentViews.getState().views;

beforeEach(() => {
  localStorage.clear();
  useAgentViews.setState({ views: {} });
  useLayout.getState().reset();
});

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("a tab's sub-views", () => {
  it("adds an agent when it is shown, and goes back to the conductor on its own id", () => {
    const { show } = useAgentViews.getState();
    show("c1", "a1");
    show("c1", "a2");
    expect(views().c1).toEqual({ open: ["a1", "a2"], shown: "a2" });

    show("c1", "a1");
    expect(views().c1).toEqual({ open: ["a1", "a2"], shown: "a1" });

    show("c1", "c1");
    expect(views().c1).toEqual({ open: ["a1", "a2"], shown: "c1" });
  });

  it("makes no entry for a tab that only ever shows its conductor", () => {
    useAgentViews.getState().show("c1", "c1");
    expect(views()).toEqual({});
  });

  it("folds an agent in without taking the tab over", () => {
    const { show, fold } = useAgentViews.getState();
    fold("c1", "a1");
    expect(views().c1).toEqual({ open: ["a1"], shown: "c1" });
    show("c1", "a2");
    fold("c1", "a3");
    expect(views().c1).toEqual({ open: ["a1", "a2", "a3"], shown: "a2" });
  });

  it("closing the one on screen shows its left-hand neighbour, and the last one closes the strip", () => {
    const { show, close } = useAgentViews.getState();
    show("c1", "a1");
    show("c1", "a2");
    show("c1", "a3");

    close("c1", "a2"); // not on screen: nothing else moves
    expect(views().c1).toEqual({ open: ["a1", "a3"], shown: "a3" });
    close("c1", "a3");
    expect(views().c1).toEqual({ open: ["a1"], shown: "a1" });
    close("c1", "a1");
    expect(views().c1).toBeUndefined();
  });

  it("lets a tab's sub-views go with the tab", () => {
    const { show, keep } = useAgentViews.getState();
    show("c1", "a1");
    show("c2", "b1");
    keep(new Set(["c2"]));
    expect(Object.keys(views())).toEqual(["c2"]);
  });

  it("survives a reload, and reads nothing it cannot trust", () => {
    useAgentViews.getState().show("c1", "a1");
    expect(readViews()).toEqual({ c1: { open: ["a1"], shown: "a1" } });

    localStorage.setItem(
      "kith-agent-views",
      JSON.stringify({
        c1: { open: ["a1", 7, "c1", "a1"], shown: "gone" },
        c2: { open: "a1" },
        c3: { open: [] },
      }),
    );
    expect(readViews()).toEqual({ c1: { open: ["a1"], shown: "c1" } });

    localStorage.setItem("kith-agent-views", "{not json");
    expect(readViews()).toEqual({});
  });

  it("works without storage at all", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });
    expect(readViews()).toEqual({});
    useAgentViews.getState().show("c1", "a1");
    expect(views().c1).toEqual({ open: ["a1"], shown: "a1" });
  });
});

/** A server whose only conversation tree is `c1 → api → schema`. Records every request. */
function server({ fail = false } = {}) {
  const calls: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    calls.push(url);
    if (fail) return new Response("no", { status: 500 });
    const id = decodeURIComponent(/conversations\/([^/]+)\/agents/.exec(url)?.[1] ?? "");
    const tree: AgentTree = { self: null, agents: [], root: id.startsWith("id-") ? "c1" : id, chain: [] };
    return new Response(JSON.stringify(tree), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  return calls;
}

/** The chat tabs in the layout, by key, and the one in front. */
function chatTabs() {
  const { tree, focused } = useLayout.getState();
  const all = panes(tree);
  const front = all.find((one) => one.id === focused);
  return {
    keys: all.flatMap((one) => one.tabs).filter((tab) => tab.surface === "chat").map(tabKey),
    front: front ? tabKey(front.tabs[front.active]) : "",
  };
}

const focusChat = (root: string) => useLayout.getState().open({ surface: "chat", conversationId: root });

describe("opening a conversation where it lives", () => {
  it("opens an agent in its conductor's tab, as that tab's sub-view — not as a tab of its own", async () => {
    const calls = server();
    const cache = testQueryClient();

    await openInConductor(cache, "id-schema", focusChat);

    expect(calls).toEqual(["/api/conversations/id-schema/agents"]);
    expect(chatTabs().keys).not.toContain("chat:id-schema");
    expect(chatTabs().front).toBe("chat:c1");
    expect(views().c1).toEqual({ open: ["id-schema"], shown: "id-schema" });
  });

  it("puts the tab back on its conductor when the conductor itself is opened", async () => {
    server();
    const cache = testQueryClient();
    await openInConductor(cache, "id-schema", focusChat);

    await openInConductor(cache, "c1", focusChat);

    expect(chatTabs().front).toBe("chat:c1");
    expect(views().c1).toEqual({ open: ["id-schema"], shown: "c1" });
  });

  it("opens a chat the window already knows is the person's at once, without asking the server", () => {
    const calls = server();
    const cache = testQueryClient();
    cache.setQueryData<ConversationSummary[]>(keys.conversations(200, ""), [
      { id: "c2", parentId: null } as ConversationSummary,
    ]);

    void openInConductor(cache, "c2", focusChat);

    // Synchronously: the tab is there before any promise has had a chance to settle.
    expect(chatTabs().front).toBe("chat:c2");
    expect(calls).toEqual([]);
    expect(views()).toEqual({});
  });

  it("lets a later open win over an agent still being looked up", async () => {
    server();
    const cache = testQueryClient();
    cache.setQueryData<ConversationSummary[]>(keys.conversations(200, ""), [
      { id: "c2", parentId: null } as ConversationSummary,
    ]);

    const slow = openInConductor(cache, "id-schema", focusChat);
    void openInConductor(cache, "c2", focusChat);
    await slow;

    expect(chatTabs().front).toBe("chat:c2");
    expect(chatTabs().keys).not.toContain("chat:c1");
    expect(views()).toEqual({});
  });

  it("drops a pending agent open when something else is started instead", async () => {
    server();
    const cache = testQueryClient();

    const slow = openInConductor(cache, "id-schema", focusChat);
    supersedeOpens();
    await slow;

    expect(chatTabs().keys).not.toContain("chat:c1");
  });

  it("opens a conversation as its own tab when the server cannot say whose it is", async () => {
    server({ fail: true });
    const cache = testQueryClient();

    await openInConductor(cache, "id-schema", focusChat);

    expect(chatTabs().front).toBe("chat:id-schema");
    expect(views()).toEqual({});
  });
});

describe("whose tree a conversation is in", () => {
  it("is read from whatever the cache already holds", () => {
    const cache = testQueryClient();
    cache.setQueryData(keys.agentTree("a1"), { self: null, agents: [], root: "c9", chain: [] });
    cache.setQueryData(keys.conversation("c3"), { id: "c3", parentId: null });
    cache.setQueryData(keys.conversation("a4"), { id: "a4", parentId: "a1" });
    // One listing caches the whole response rather than its rows.
    cache.setQueryData(keys.conversations(500, ""), { conversations: [{ id: "c5", parentId: null }] });
    // A conductor's tree, as the sidebar's fold or the map fetched it, holding a nested agent.
    cache.setQueryData(keys.agentTree("c6"), {
      self: null,
      root: "c6",
      chain: [],
      agents: [{ id: "a6", name: "api", agents: [{ id: "a7", name: "schema", agents: [] }] }],
    });

    expect(knownConductor(cache, "a1")).toBe("c9");
    expect(knownConductor(cache, "a7")).toBe("c6");
    expect(knownConductor(cache, "c3")).toBe("c3");
    expect(knownConductor(cache, "c5")).toBe("c5");
    // An agent's record names its handler, not its conductor — that takes the tree.
    expect(knownConductor(cache, "a4")).toBe("");
    expect(knownConductor(cache, "nobody")).toBe("");
  });

  it("asks the server only when the cache cannot say", async () => {
    const calls = server();
    const cache = testQueryClient();
    expect(await conductorOf(cache, "id-api")).toBe("c1");
    expect(await conductorOf(cache, "id-api")).toBe("c1");
    expect(calls).toHaveLength(1);
  });
});
