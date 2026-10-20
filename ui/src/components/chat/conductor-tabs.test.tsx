/**
 * The strip in a conductor's tab: the conductor, then the agents opened in it.
 *
 * Two promises worth pinning. It is only there once an agent has been opened — a chat with no
 * agents looked at has nothing to switch between. And it can come and go without touching the
 * body under it, because the body there is a whole chat: a runtime, a thread and a scroll position
 * that a remount would throw away.
 */
import { useEffect } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fireEvent, render, screen, within } from "@/test/render";
import type { AgentNode, AgentTree } from "@/lib/backend";

import { ConductorTabs } from "./conductor-tabs";

function agent(id: string, name: string, over: Partial<AgentNode> = {}): AgentNode {
  return {
    id,
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
    agents: [],
    ...over,
  };
}

const TREE: AgentTree = {
  self: {
    id: "c1",
    title: "Build the payroll module",
    name: "conductor",
    working: false,
    tokens: 0,
    costUsd: 0,
    seconds: 0,
    contextTokens: 0,
    modelUsed: "",
  },
  root: "c1",
  chain: [],
  agents: [agent("a1", "api", { state: "working", working: true }), agent("a2", "docs")],
};

function server() {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify(TREE), { status: 200, headers: { "Content-Type": "application/json" } }),
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("a conductor's tab", () => {
  it("has no strip until an agent has been opened in it", () => {
    server();
    render(
      <ConductorTabs root="c1" open={[]} shown="c1" onShow={() => {}} onClose={() => {}}>
        <p>the chat</p>
      </ConductorTabs>,
    );
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.getByText("the chat")).toBeInTheDocument();
  });

  it("lists the conductor first, then each agent opened in it, and marks the one on screen", async () => {
    server();
    render(
      <ConductorTabs root="c1" open={["a1", "a2"]} shown="a1" onShow={() => {}} onClose={() => {}}>
        <p>the chat</p>
      </ConductorTabs>,
    );

    const strip = screen.getByRole("tablist");
    expect(await within(strip).findByRole("tab", { name: /Build the payroll module/ })).toHaveAttribute(
      "aria-selected",
      "false",
    );
    const tabs = within(strip).getAllByRole("tab");
    expect(tabs.map((one) => one.textContent)).toEqual(["Build the payroll module", "api", "docs"]);
    expect(tabs[1]).toHaveAttribute("aria-selected", "true");
    expect(tabs[1]).toHaveAttribute("title", "api — working");
  });

  it("switches on a click, and closes on the × without switching", async () => {
    server();
    const onShow = vi.fn();
    const onClose = vi.fn();
    render(
      <ConductorTabs root="c1" open={["a1", "a2"]} shown="a1" onShow={onShow} onClose={onClose}>
        <p>the chat</p>
      </ConductorTabs>,
    );

    fireEvent.click(await screen.findByRole("tab", { name: /Build the payroll module/ }));
    expect(onShow).toHaveBeenLastCalledWith("c1");
    fireEvent.click(screen.getByRole("tab", { name: /docs/ }));
    expect(onShow).toHaveBeenLastCalledWith("a2");

    onShow.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "Close docs" }));
    expect(onClose).toHaveBeenCalledWith("a2");
    expect(onShow).not.toHaveBeenCalled();
  });

  it("moves along the strip with the arrow keys", async () => {
    server();
    const onShow = vi.fn();
    render(
      <ConductorTabs root="c1" open={["a1", "a2"]} shown="a1" onShow={onShow} onClose={() => {}}>
        <p>the chat</p>
      </ConductorTabs>,
    );
    const strip = screen.getByRole("tablist");
    fireEvent.keyDown(strip, { key: "ArrowRight" });
    expect(onShow).toHaveBeenLastCalledWith("a2");
    fireEvent.keyDown(strip, { key: "Home" });
    expect(onShow).toHaveBeenLastCalledWith("c1");
  });

  it("does not rebuild the body when the strip appears or goes", () => {
    server();
    const mounts = vi.fn();
    function Body() {
      useEffect(() => {
        mounts();
      }, []);
      return <p>the chat</p>;
    }
    const props = { root: "c1", shown: "c1", onShow: () => {}, onClose: () => {} };
    const { rerender } = render(
      <ConductorTabs {...props} open={[]}>
        <Body />
      </ConductorTabs>,
    );
    rerender(
      <ConductorTabs {...props} open={["a1"]}>
        <Body />
      </ConductorTabs>,
    );
    expect(screen.getByRole("tablist")).toBeInTheDocument();
    rerender(
      <ConductorTabs {...props} open={[]}>
        <Body />
      </ConductorTabs>,
    );
    expect(mounts).toHaveBeenCalledTimes(1);
  });
});
