/**
 * A message posted for a draft tab from the Control Panel is sent by that tab's pane — as one
 * turn, through the ordinary adapter, in the project it was posted with.
 *
 * Counted at the wire: the thing that must never happen twice is `POST /api/chat`, so that is
 * what these assert on, under the ways a pane gets mounted more than once.
 */
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { act, render, screen, waitFor } from "@/test/render";
import { ConfirmProvider } from "@/components/ui/confirm";
import { ChatPane } from "./chat-pane";
import { useLayout } from "@/components/shell/layout/store";
import { pane } from "@/components/shell/layout/tree";
import { post, waitingFor } from "@/lib/outbox";

const ASK = "About #164 (“The switcher works”):\n\nwhy B?";

/** Every `POST /api/chat` the pane made, as parsed bodies. */
let sent: { messages: { role: string; content: string }[]; projectId?: number; conversationId?: string }[] = [];

function server() {
  sent = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/api/chat") && init?.method === "POST") {
      sent.push(JSON.parse(String(init.body)));
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(
            new TextEncoder().encode(JSON.stringify({ type: "delta", role: "text", text: "on it" }) + "\n"),
          );
          controller.close();
        },
      });
      return new Response(stream, { status: 200, headers: { "X-Kith-Turn": "t1" } });
    }
    if (url.includes("/checkpoints")) {
      return new Response(JSON.stringify({ checkpoints: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (url.includes("/api/conversations/")) {
      return new Response(
        JSON.stringify({ id: "c1", title: "", projectId: null, working: false, windowStart: 0, timeline: [] }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
  });
}

function draft(uid: string, strict = false) {
  const ui = (
    <ConfirmProvider>
      <ChatPane conversationId="" uid={uid} />
    </ConfirmProvider>
  );
  return render(strict ? <StrictMode>{ui}</StrictMode> : ui);
}

beforeEach(() => {
  useLayout.setState({ tree: pane([{ surface: "chat", conversationId: "" }]), focused: "" });
  server();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("a message posted for a draft chat", () => {
  it("is sent once, as the first turn, in the project it was posted with", async () => {
    post("p-mount", { text: ASK, projectId: 42 });
    draft("p-mount");
    await waitFor(() => expect(screen.queryAllByText("on it")).toHaveLength(1));
    expect(sent).toHaveLength(1);
    expect(sent[0].projectId).toBe(42);
    expect(sent[0].conversationId).toBeUndefined();
    expect(sent[0].messages.at(-1)).toMatchObject({ role: "user", content: ASK });
    expect(waitingFor("p-mount")).toBe(false);
  });

  it("is sent once under React's development double-mount", async () => {
    post("p-strict", { text: ASK, projectId: null });
    draft("p-strict", true);
    await waitFor(() => expect(screen.queryAllByText("on it")).toHaveLength(1));
    expect(sent).toHaveLength(1);
    expect(sent[0].projectId).toBeUndefined();
  });

  it("is not sent again when the pane remounts", async () => {
    post("p-remount", { text: ASK, projectId: 1 });
    const first = draft("p-remount");
    await waitFor(() => expect(sent).toHaveLength(1));
    first.unmount();
    draft("p-remount");
    await act(async () => {
      await new Promise((done) => setTimeout(done, 30));
    });
    expect(sent).toHaveLength(1);
  });

  it("reaches a pane that was already mounted under the panel", async () => {
    draft("p-live");
    await act(async () => {
      await new Promise((done) => setTimeout(done, 10));
    });
    expect(sent).toHaveLength(0);
    act(() => post("p-live", { text: ASK, projectId: 9 }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0].projectId).toBe(9);
  });

  it("goes into the composer, unsent, when the tab is no longer a fresh chat", async () => {
    useLayout.setState({ tree: pane([{ surface: "chat", conversationId: "c1" }]), focused: "" });
    post("p-named", { text: "About source #3 (“x”):\n\nwhat is it?", projectId: null });
    render(
      <ConfirmProvider>
        <ChatPane conversationId="c1" uid="p-named" />
      </ConfirmProvider>,
    );
    await act(async () => {
      await new Promise((done) => setTimeout(done, 30));
    });
    expect(sent).toHaveLength(0);
    expect(waitingFor("p-named")).toBe(false);
    // In the box for you to send or not — not lost.
    await waitFor(() => expect(document.body.textContent).toContain("what is it?"));
  });
});
