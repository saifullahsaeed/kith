/**
 * Opening a conversation asks for a page of it.
 *
 * The bug this holds shut: the interface has always rendered the last forty turns, but it asked
 * the server for all of them and did the slicing here. On the largest real transcript that is
 * 22.83 MB downloaded and parsed so that 1.06 MB could be shown, with the rest kept in state
 * for a "load earlier" button most opens never touch.
 *
 * The server's default is a page now, so an unadorned request is already bounded — these
 * assert the client's side of that contract: that it does not ask for everything, and that
 * paging is expressed as "what comes before the page I have" rather than an offset computed
 * on both sides of the wire, which is the arithmetic that goes wrong at exactly the boundary
 * nobody looks at.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchConversation } from "./conversations";

const empty = {
  id: "c-1",
  timeline: [],
  turnCount: 0,
  windowStart: 0,
  hasMore: false,
};

let calls: string[];

beforeEach(() => {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      calls.push(String(url));
      return new Response(JSON.stringify(empty), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetching a conversation", () => {
  it("asks for the server's page by default, without spelling one out", async () => {
    await fetchConversation("c-1");

    expect(calls).toEqual(["/api/conversations/c-1"]);
  });

  it("never asks for everything by accident", async () => {
    await fetchConversation("c-1");

    expect(calls[0]).not.toContain("turns=0");
  });

  it("asks for the page before the one it holds", async () => {
    await fetchConversation("c-1", { turns: 40, before: 160 });

    expect(calls[0]).toBe("/api/conversations/c-1?turns=40&before=160");
  });

  it("sends `before` of zero rather than dropping it", async () => {
    /* `before=0` means "everything above the very top", which is an empty page and the correct
     * end of a scroll. A falsy check here would drop the parameter and fetch the newest page
     * again — an infinite scroll that keeps handing back the bottom of the conversation. */
    await fetchConversation("c-1", { before: 0 });

    expect(calls[0]).toBe("/api/conversations/c-1?before=0");
  });

  it("reports a failure rather than returning half a conversation", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("nope", { status: 500 })));

    await expect(fetchConversation("c-1")).rejects.toThrow(/could not open/);
  });
});
