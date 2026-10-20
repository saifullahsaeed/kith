/**
 * What a `start_agents` call leaves in the thread.
 *
 * Like `ask`, the call's two halves live in different places: who was asked for, and with what
 * task, is in the arguments; who was actually started, on which branch — or why not — is in the
 * result. The card reads both, and the shapes here are the server's (`services/agents.start`).
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { OpenConversation } from "@/lib/open-conversation";

import { ToolResultBody } from "./tool-result";

const ARGS = {
  agents: [
    { name: "api", task: "Build the REST routes for devices.\n\nKeep to the contract in docs/api.md." },
    { name: "web", task: "The devices screen.", workspace: "shared" },
    { name: "api", task: "A second one with a taken name." },
  ],
};

const RESULT = {
  ok: true,
  result: {
    agents: [
      { name: "api", id: "a1", workspace: "own", folder: "/x/wt/api", branch: "kith/api" },
      { name: "web", id: "a2", workspace: "shared", folder: "/x" },
      { name: "api", error: "you already have an open agent with that name — message it, or pick another" },
    ],
    note: "Your checkout has uncommitted changes (a.py).",
  },
};

describe("the agents a turn started", () => {
  it("names each, with its branch and the start of its task, and opens one on click", () => {
    const opened = vi.fn();
    render(
      <OpenConversation.Provider value={opened}>
        <ToolResultBody name="start_agents" args={ARGS} result={RESULT} />
      </OpenConversation.Provider>,
    );

    expect(screen.getByText("kith/api")).toBeInTheDocument();
    expect(screen.getByText(/Build the REST routes for devices/)).toBeInTheDocument();
    expect(screen.getByText("in this folder")).toBeInTheDocument();
    expect(screen.getByText(/you already have an open agent with that name/)).toBeInTheDocument();
    expect(screen.getByText(/uncommitted changes/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^web/ }));
    expect(opened).toHaveBeenCalledWith("a2");
    // The one that was refused is not a link: there is no conversation behind it.
    expect(screen.getAllByRole("button")).toHaveLength(2);
  });
});
