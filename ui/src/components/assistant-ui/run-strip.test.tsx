/**
 * A turn with work in it is drawn as a run strip of beats, then the answer.
 *
 * What is guarded here is the part that is easy to break without noticing: every part is drawn
 * exactly once (the answer is not also a beat, a beat's sentence is not also prose), a finished
 * turn opens at Story (sentences listed, calls folded), and opening a beat shows its calls.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fireEvent, render, screen, waitFor } from "@/test/render";
import { ConfirmProvider } from "@/components/ui/confirm";
import { ChatPane } from "@/components/chat/chat-pane";
import { useLayout } from "@/components/shell/layout/store";
import { pane } from "@/components/shell/layout/tree";

function detail(parts: unknown[]) {
  return {
    id: "c1",
    title: "a chat",
    sessionId: "s1",
    createdAt: "",
    updatedAt: "",
    messageCount: 2,
    lastSaid: "",
    transcript: "",
    projectId: null,
    working: false,
    waiting: false,
    turnCount: 2,
    windowStart: 0,
    hasMore: false,
    timeline: [
      { role: "user", parts: [{ kind: "text", text: "fix the table" }], at: "" },
      { role: "assistant", parts, at: "" },
    ],
  };
}

function server(parts: unknown[]) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    const json = (body: unknown) =>
      new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    if (url.includes("/attach")) return new Response(null, { status: 204 });
    if (url.includes("/checkpoints")) return json({ checkpoints: [] });
    if (url.includes("/api/conversations/")) return json(detail(parts));
    return json({});
  });
}

function open() {
  return render(
    <ConfirmProvider>
      <ChatPane conversationId="c1" uid="run-strip" />
    </ConfirmProvider>,
  );
}

const times = (text: string) => screen.queryAllByText(text).length;

beforeEach(() => {
  useLayout.setState({ tree: pane([{ surface: "chat", conversationId: "c1" }]), focused: "" });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the run strip", () => {
  it("lists each sentence once as a beat, folds the calls, and draws the answer once", async () => {
    server([
      { kind: "text", text: "Found the sort that throws." },
      { kind: "tool", id: "c1", name: "read_file", arguments: { path: "app.js" }, result: { ok: true, result: "x" } },
      { kind: "text", text: "Reproduced it first." },
      {
        kind: "tool",
        id: "c2",
        name: "shell",
        arguments: { command: "node --test" },
        result: { ok: false, error: "exit 1: TypeError" },
      },
      { kind: "tool", id: "c3", name: "shell", arguments: { command: "node --test" }, result: { ok: true, result: { exit: 0 } } },
      { kind: "text", text: "All fixed." },
    ]);
    open();

    await waitFor(() => expect(times("All fixed.")).toBe(1));
    expect(screen.getByText("Worked")).toBeTruthy();
    expect(times("Found the sort that throws.")).toBe(1);
    expect(times("Reproduced it first.")).toBe(1);
    // A failure that the same tool then got past is said as such, on the beat's row.
    expect(screen.getByText("1 failed")).toBeTruthy();
    expect(screen.getByText("recovered")).toBeTruthy();

    // Story depth: the calls are folded until their beat is opened.
    expect(document.querySelectorAll('[data-slot="tool-fallback-trigger"]').length).toBe(0);
    fireEvent.click(screen.getAllByRole("button", { name: "Show these steps" })[0]!);
    await waitFor(() =>
      expect(document.querySelectorAll('[data-slot="tool-fallback-trigger"]').length).toBe(1),
    );
  });

  it("draws a turn with no calls as prose, with no strip", async () => {
    server([
      { kind: "reasoning", text: "short thought" },
      { kind: "text", text: "Just an answer." },
    ]);
    open();

    await waitFor(() => expect(times("Just an answer.")).toBe(1));
    expect(document.querySelector('[data-slot="kith_run-strip"]')).toBeNull();
  });
});
