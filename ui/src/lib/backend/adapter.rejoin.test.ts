/**
 * A dropped stream is not a failed turn.
 *
 * Waking from sleep, or Chromium restarting its network service, cancels every request the window
 * has open. The turn runs on the server and never notices. Until 2026-10-21 the window showed
 * "Didn't finish" and stopped there, over a turn that was still working — and the next message
 * started a second turn on top of it. Now it attaches again, skips what it already showed, and
 * carries on; if the turn ended while it was away, it says so, so the pane can fetch the end.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { createBackendAdapter } from "./adapter";

const line = (event: object) => `${JSON.stringify(event)}\n`;
const said = (text: string) => line({ type: "delta", role: "text", text });

/** A response whose body sends `chunks`, then either ends or fails the way a dropped socket does. */
function stream(chunks: string[], { drop = false, turn = "t1" } = {}): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(new TextEncoder().encode(chunk));
      // Later, not now: erroring a stream throws away whatever it still has queued, and a socket
      // that drops has already delivered what it delivered.
      if (drop) setTimeout(() => controller.error(new TypeError("network error")), 20);
      else controller.close();
    },
  });
  return new Response(body, { status: 200, headers: { "X-Kith-Turn": turn } });
}

/** Every text part of the last update, joined — what the person sees at the end. */
async function run(onTurn?: (turnId: string, readToEnd?: boolean) => void) {
  const adapter = createBackendAdapter({ get: () => "c1", set: () => {}, ...(onTurn ? { onTurn } : {}) });
  const running = adapter.run({
    messages: [{ role: "user", content: [{ type: "text", text: "go" }] }],
    abortSignal: new AbortController().signal,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } as any) as AsyncGenerator<{ content: { type: string; text?: string }[] }>;
  let last: { type: string; text?: string }[] = [];
  for await (const update of running) last = update.content;
  return last
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("");
}

describe("a turn whose stream drops", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("is rejoined, and what was already shown is not shown twice", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url === "/api/chat") return stream([line({ type: "conversation", id: "c1" }), said("one "), said("two ")], { drop: true });
      // `/attach` replays the turn from its first line, then carries on.
      return stream([said("one "), said("two "), said("three"), line({ type: "done" })]);
    });
    vi.stubGlobal("fetch", fetchMock);

    expect(await run()).toBe("one two three");
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual(["/api/chat", "/api/chat/c1/attach"]);
  });

  it("says it lost the end when the turn finished while the window was away", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url === "/api/chat" ? stream([said("one ")], { drop: true }) : new Response(null, { status: 204 }),
      ),
    );
    const released: (boolean | undefined)[] = [];

    const shown = await run((turnId, readToEnd) => {
      if (!turnId) released.push(readToEnd);
    });

    expect(shown).toBe("one ");
    expect(released).toEqual([false]);
  });

  it("does not follow a different turn that started meanwhile", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url === "/api/chat"
          ? stream([said("mine ")], { drop: true })
          : stream([said("someone else's"), line({ type: "done" })], { turn: "t2" }),
      ),
    );
    expect(await run()).toBe("mine ");
  });

  it("still fails when the turn itself fails", async () => {
    const fetchMock = vi.fn(async () => stream([said("one "), line({ type: "error", message: "the model said no" })]));
    vi.stubGlobal("fetch", fetchMock);

    await expect(run()).rejects.toThrow("the model said no");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
