/**
 * The project a chat was started in rides with the turn that creates the conversation.
 *
 * It used to be written afterwards, over a second request, once the stream had reported an id —
 * so the first turn of every chat was assembled on the server with no project bound. That is the
 * turn where somebody says what the work is, and it was the one turn built without the project's
 * memory, plan or task columns, with the whole cross-project listing in their place.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

import { createBackendAdapter } from "./adapter";

function bodyOf(call: unknown[]): Record<string, unknown> {
  return JSON.parse(String((call[1] as RequestInit).body));
}

/** One turn's worth of the NDJSON the server streams, so `run` reaches the end. */
function streamed(): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('{"type":"done"}\n'));
      controller.close();
    },
  });
  return new Response(body, { status: 200, headers: { "content-type": "application/x-ndjson" } });
}

async function drain(adapter: ReturnType<typeof createBackendAdapter>) {
  const messages = [{ role: "user" as const, content: [{ type: "text" as const, text: "hi" }] }];
  /* `run` is typed as "a promise or an async generator" because the interface allows either.
   * This adapter is always the generator, and awaiting a union is not iterable, so the cast is
   * about the declared type rather than about the value. */
  const running = adapter.run({
    messages,
    abortSignal: new AbortController().signal,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } as any) as AsyncGenerator<unknown>;
  for await (const _ of running) {
    /* the events themselves are `stream.test.ts`'s subject, not this one's */
  }
}

describe("the project a chat is started in", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn(async () => streamed());
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("is sent with the first turn, which has no conversation id yet", async () => {
    await drain(createBackendAdapter({ get: () => "", set: () => {}, project: () => 17 }));

    const body = bodyOf(fetchMock.mock.calls[0]);
    expect(body.projectId).toBe(17);
    expect(body.conversationId).toBeUndefined();
  });

  it("is omitted when the chat was not started in a project", async () => {
    await drain(createBackendAdapter({ get: () => "", set: () => {}, project: () => null }));

    expect(bodyOf(fetchMock.mock.calls[0])).not.toHaveProperty("projectId");
  });

  it("is omitted entirely when nothing supplies one", async () => {
    // The reminder path and anything else building an adapter without the hook.
    await drain(createBackendAdapter({ get: () => "c1", set: () => {} }));

    const body = bodyOf(fetchMock.mock.calls[0]);
    expect(body).not.toHaveProperty("projectId");
    expect(body.conversationId).toBe("c1");
  });
});
