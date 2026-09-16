/**
 * A pane opened onto a turn that is already running.
 *
 * This is the case the pane got wrong in both directions, and it is worth stating why it is hard:
 * the same turn arrives twice. `GET /api/conversations/<id>` builds its timeline from the
 * transcript, and the recorder writes the turn's parts *as they happen*, so a conversation read
 * mid-turn already contains the half-written reply. `GET /api/chat/<id>/attach` then hands over
 * the same reply as a live stream.
 *
 * The pane used to fetch both at once from separate effects and sort it out by asking `isRunning`
 * as each landed. Whichever won decided which bug you got: the page first meant the answer was
 * rendered twice, the stream first meant the history above it was dropped and never restored.
 *
 * So both orders are tested, and the assertion is a count rather than a presence — "the reply is
 * on screen" was true in the broken version too.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { render, screen, waitFor } from "@/test/render";
import { ConfirmProvider } from "@/components/ui/confirm";
import { ChatPane } from "./chat-pane";
import { useLayout } from "@/components/shell/layout/store";
import { pane } from "@/components/shell/layout/tree";

const TURN = "turn-abc";

/** The conversation as the server tells it, including the turn that is still being written. */
function detail(extra: Record<string, unknown> = {}) {
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
    working: true,
    waiting: false,
    turnCount: 2,
    windowStart: 0,
    hasMore: false,
    timeline: [
      { role: "user", parts: [{ kind: "text", text: "the question" }], at: "" },
      // Stamped with the turn that is writing it — the whole point. A pane streaming this turn
      // must drop this copy rather than render it beside the live one.
      { role: "assistant", parts: [{ kind: "text", text: "the answer" }], at: "", turn: TURN },
    ],
    ...extra,
  };
}

/** The live turn, as `/attach` hands it over: the same words, plus a header naming the turn.
 *
 * The stream says one word more than the transcript has flushed, and that is what makes the
 * assertions below decidable rather than timed. The stored copy renders as exactly "the answer";
 * the streamed one as "the answer, live". So "the stream has landed" and "the stored copy was
 * dropped" are two different queries over the DOM, and neither is a guess about how long to wait.
 * Testing this with identical text meant sleeping and hoping — which passes on a quiet machine
 * and fails on a busy one, for reasons that have nothing to do with the code under test. */
function attached(delay = 0) {
  const body =
    JSON.stringify({ type: "delta", role: "text", text: "the answer" }) +
    "\n" +
    JSON.stringify({ type: "delta", role: "text", text: ", live" }) +
    "\n";
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      if (delay) await new Promise((done) => setTimeout(done, delay));
      controller.enqueue(new TextEncoder().encode("\n" + body));
      controller.close();
    },
  });
  return new Response(stream, { status: 200, headers: { "X-Kith-Turn": TURN } });
}

/** The pane, inside the one provider the real app puts above it. */
function open(uid: string) {
  return render(
    <ConfirmProvider>
      <ChatPane conversationId="c1" uid={uid} />
    </ConfirmProvider>,
  );
}

/** How many times a piece of text is on screen. */
function times(text: string): number {
  return screen.queryAllByText(text).length;
}

beforeEach(() => {
  useLayout.setState({ tree: pane([{ surface: "chat", conversationId: "c1" }]), focused: "" });
});

afterEach(() => {
  vi.restoreAllMocks();
});

/** Answer the two requests the pane makes, with the page delayed by `pageDelay` ms. */
function server({ pageDelay = 0, attachDelay = 0 } = {}) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/attach")) return attached(attachDelay);
    // Before the conversation branch: the checkpoint route lives under the same prefix, and
    // answering it with a conversation is how this mock first made the thread crash.
    if (url.includes("/checkpoints")) {
      return new Response(JSON.stringify({ checkpoints: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (url.includes("/api/conversations/")) {
      if (pageDelay) await new Promise((done) => setTimeout(done, pageDelay));
      return new Response(JSON.stringify(detail()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
  });
}

describe("a pane opened on a running turn", () => {
  it("renders the reply once when the stored page arrives first", async () => {
    server({ attachDelay: 20 });
    open("tab-1");

    // The stream has landed…
    await waitFor(() => expect(times("the answer, live")).toBe(1));
    // …and the transcript's copy of the same turn is not beside it.
    expect(times("the answer")).toBe(0);
  });

  it("renders the reply once when the live stream arrives first", async () => {
    server({ pageDelay: 20 });
    open("tab-2");

    await waitFor(() => expect(times("the answer, live")).toBe(1));
    expect(times("the answer")).toBe(0);
  });

  it("keeps the history above the reply, whichever arrives first", async () => {
    /* The other half of the same bug. The stream winning the race meant `reset` was skipped on a
     * guard nothing ever retried, so the conversation opened showing a reply and nothing before
     * it — no question, no history, as if the chat had begun with an answer. */
    server({ pageDelay: 20 });
    open("tab-3");

    await waitFor(() => expect(times("the answer, live")).toBe(1));
    expect(times("the question")).toBe(1);
  });
});
