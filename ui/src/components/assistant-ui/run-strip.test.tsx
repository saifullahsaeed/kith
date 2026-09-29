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

  it("draws an answer he gave mid-turn at full size, between two strips, not clamped in a beat", async () => {
    const reply =
      "Short answer: a weekend to post a review.\n\n## The architecture you're building\n\nWebhook, then a queue.";
    server([
      { kind: "text", text: "Checking what exists." },
      { kind: "tool", id: "c1", name: "web_search", arguments: { query: "pr review bots" }, result: { ok: true, result: [] } },
      { kind: "text", text: reply },
      { kind: "tool", id: "c2", name: "shell", arguments: { command: "ls" }, result: { ok: true, result: { exit: 0 } } },
      { kind: "text", text: "Scaffolded." },
    ]);
    open();

    const heading = await screen.findByRole("heading", { name: "The architecture you're building" });
    // Once, and outside every strip: an output is not a beat's sentence.
    expect(screen.getAllByRole("heading", { name: "The architecture you're building" })).toHaveLength(1);
    expect(heading.closest('[data-slot="kith_run-strip"]')).toBeNull();
    expect(heading.closest("[data-kith-answer]")).not.toBeNull();
    expect(times("Scaffolded.")).toBe(1);

    const strips = document.querySelectorAll('[data-slot="kith_run-strip"]');
    expect(strips).toHaveLength(2);
    // The last strip speaks for the turn; the one above the mid-turn answer says what it did.
    expect(strips[1]!.textContent).toContain("Worked");
    expect(strips[0]!.textContent).toContain("1 search");
    expect(strips[0]!.textContent).not.toContain("Worked");
    // In the order it happened: strip, the reply, strip, the answer.
    const order = [...document.querySelectorAll('[data-slot="kith_run-strip"], [data-kith-answer]')].map(
      (el) => (el.matches("[data-kith-answer]") ? "answer" : "strip"),
    );
    expect(order).toEqual(["strip", "answer", "strip", "answer"]);
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
