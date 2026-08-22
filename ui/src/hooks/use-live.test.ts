/**
 * One connection, and what each event on it makes stale.
 *
 * Two contracts are asserted here, and both were comments before.
 *
 * **The connection count.** This started as a bug invisible from the behaviour: every widget
 * updated correctly and the app froze anyway. `useChanges` opened one `EventSource` per subscriber,
 * on the reasoning — written into the hook — that "SSE over HTTP/2 multiplexes on a single
 * connection". Kith is served over plain HTTP/1.1, where Chromium allows six connections per
 * origin, so each subscriber took a whole one. Measured on 2026-08-14: the renderer held exactly
 * six and had issued no request of any kind for four minutes while a turn sat parked on an `ask` —
 * the question card could not be fetched, health could not be polled, and Cmd-R could not fetch the
 * document. The freeze was the pool, not the renderer. The count is therefore a contract.
 *
 * **The invalidation.** Which is the new half. A change event no longer hands a callback to every
 * widget that wants one; it invalidates keys, and the cache decides what to ask for. So what has to
 * be true is that each kind reaches the right keys, that a burst becomes one refetch rather than
 * three, and that a gap in the ids is turned into a resync — because that last one is what made the
 * eleven polling timers removable.
 */
import { createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { useLiveUpdates, useServerEvent } from "./use-live";
import { STALE_ON } from "@/lib/query-keys";
import type { ChangeKind } from "@/lib/backend/events";

interface FakeSource {
  url: string;
  closed: boolean;
}

interface FakeSourceClass {
  open: FakeSource[];
  epoch: string;
  emit(kind: string, conversation?: string, id?: number): void;
  send(type: string, data: unknown, id?: number): void;
}

const Sources = () => (globalThis as unknown as { EventSource: FakeSourceClass }).EventSource;
const live = () => Sources().open.filter((source) => !source.closed);

/** A client whose invalidations can be watched without any of them actually fetching. */
function watched() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(client, "invalidateQueries").mockResolvedValue(undefined);
  return { client, invalidate };
}

/** `useLiveUpdates` needs a client in context. `createElement` rather than JSX so this stays a
 *  `.ts` file — the provider is the only element involved. */
function mount(client: QueryClient) {
  return renderHook(() => useLiveUpdates(), {
    wrapper: ({ children }) => createElement(QueryClientProvider, { client }, children),
  });
}

describe("the one connection", () => {
  it("opens a single stream at /api/events however many things listen", () => {
    renderHook(() => {
      useServerEvent("task", () => {});
      useServerEvent("turn", () => {});
      useServerEvent(["project", "message"], () => {});
      useServerEvent("process", () => {}, "c-1");
    });

    expect(live()).toHaveLength(1);
    expect(live()[0]!.url).toBe("/api/events");
  });

  it("stays up while anything is listening and closes with the last", () => {
    const staying = vi.fn();
    const leaving = renderHook(() => useServerEvent("task", () => {}));
    const remaining = renderHook(() => useServerEvent("task", staying));

    leaving.unmount();
    expect(live()).toHaveLength(1);
    Sources().emit("task");
    expect(staying).toHaveBeenCalledTimes(1);

    remaining.unmount();
    expect(live()).toHaveLength(0);
  });

  it("does not open one at all inside the desktop shell", () => {
    // The shell holds the stream in its main process and pushes over IPC — see
    // desktop/src/server/events.ts. A renderer that opened its own would put the connection back
    // in Chromium's pool and lose the cursor on every reload, which is the whole point of moving it.
    const forward = vi.fn();
    (window as unknown as { kith: unknown }).kith = { onEvent: () => () => {} };

    renderHook(() => useServerEvent("task", forward));

    expect(live()).toHaveLength(0);
  });

  it("filters per listener rather than per connection", () => {
    const onTask = vi.fn();
    const onTurn = vi.fn();
    renderHook(() => {
      useServerEvent("task", onTask);
      useServerEvent("turn", onTurn);
    });

    Sources().emit("task");

    // Sharing a socket must not mean sharing a filter.
    expect(onTask).toHaveBeenCalledTimes(1);
    expect(onTurn).not.toHaveBeenCalled();
  });

  it("ignores another conversation's events when scoped to one", () => {
    const mine = vi.fn();
    renderHook(() => useServerEvent("process", mine, "c-1"));

    Sources().emit("process", "c-2");
    expect(mine).not.toHaveBeenCalled();

    Sources().emit("process", "c-1");
    expect(mine).toHaveBeenCalledTimes(1);
  });

  it("always delivers events that belong to no conversation", () => {
    // A change made from the control panel carries no conversation, and a consumer scoped to one
    // that ignored those would miss the board moving.
    const mine = vi.fn();
    renderHook(() => useServerEvent("task", mine, "c-1"));

    Sources().emit("task", "");
    expect(mine).toHaveBeenCalledTimes(1);
  });
});

describe("what a change makes stale", () => {
  beforeEach(() => vi.useFakeTimers());

  it("invalidates the keys mapped to the kind, and nothing else", () => {
    const { client, invalidate } = watched();
    mount(client);

    Sources().emit("permission");
    vi.advanceTimersByTime(60);

    expect(invalidate).toHaveBeenCalledTimes(1);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["permissions"] });
  });

  it("coalesces a burst into one round of invalidation per key", () => {
    // A turn that files three tasks publishes three `task` events inside a few milliseconds. Before
    // the cache, that was three rounds of every subscriber's own refetch.
    const { client, invalidate } = watched();
    mount(client);

    Sources().emit("task");
    Sources().emit("task");
    Sources().emit("task");
    vi.advanceTimersByTime(60);

    expect(invalidate).toHaveBeenCalledTimes(STALE_ON.task.length);
  });

  it("treats a gap in the ids as a resync", () => {
    /* The property that lets the timers go. A subscriber queue that overflowed, or a frame lost in
     * transit, means the client cannot know what it missed — so it stops guessing and treats
     * everything as stale. Without this, a dropped event is a screen that is quietly wrong, which
     * is exactly what the eleven polls were insuring against. */
    const { client, invalidate } = watched();
    mount(client);

    Sources().emit("task", "", 1);
    vi.advanceTimersByTime(60);
    invalidate.mockClear();

    Sources().emit("task", "", 7); // 2..6 never arrived
    vi.advanceTimersByTime(60);

    // Everything, which is what `invalidateQueries` with no filter means.
    expect(invalidate).toHaveBeenCalledTimes(1);
    expect(invalidate).toHaveBeenCalledWith();
  });

  it("treats a restart as a resync even when the numbers look ordinary", () => {
    /* The case a gap check cannot catch. A restarted server numbers from one again, so a client
     * that reconnects *late* — after the fresh run has published past where it got to — sees a
     * number that looks like an ordinary next position. The server refuses a cursor from another
     * run; this is the same judgement on this side, which also covers the desktop shell, where the
     * main process reconnects on the page's behalf and the page never sees the outage. */
    const { client, invalidate } = watched();
    mount(client);

    Sources().emit("task", "", 40);
    vi.advanceTimersByTime(60);
    invalidate.mockClear();

    Sources().epoch = "run-2";
    Sources().emit("task", "", 41); // the very next number, from a different sequence
    vi.advanceTimersByTime(60);

    expect(invalidate).toHaveBeenCalledWith();
  });

  it("does not cry restart when the server never names a run", () => {
    // A bare numeric id is what a server too old to send an epoch writes, and it must keep the
    // behaviour it had rather than resyncing on every event.
    const { client, invalidate } = watched();
    mount(client);

    Sources().epoch = "";
    Sources().emit("task", "", 1);
    Sources().emit("task", "", 2);
    vi.advanceTimersByTime(60);

    expect(invalidate).not.toHaveBeenCalledWith();
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["brain"] });
  });

  it("treats the server's own resync the same way", () => {
    // Sent when the cursor is older than the server's log, or when the process restarted under it.
    const { client, invalidate } = watched();
    mount(client);

    Sources().send("resync", {});
    vi.advanceTimersByTime(60);

    expect(invalidate).toHaveBeenCalledWith();
  });

  it("does not invalidate anything for an activity line", () => {
    // The feed is data the stream carries, not a hint that something needs refetching. Treating it
    // as one would mean invalidating on every tool call of every turn.
    const { client, invalidate } = watched();
    mount(client);

    Sources().send("activity", { kind: "tool", text: "read a file" });
    vi.advanceTimersByTime(60);

    expect(invalidate).not.toHaveBeenCalled();
  });
});

describe("the map itself", () => {
  it("has an entry for every kind the server can publish", () => {
    /* A kind with no entry is a change nothing responds to, and from the interface that reads as a
     * feature that is merely quiet. The server asserts the other half — that every kind it declares
     * is published by something — in `test_the_interface_is_told_what_changed.py`. */
    const kinds: ChangeKind[] = [
      "turn",
      "task",
      "project",
      "message",
      "process",
      "workspace",
      "question",
      "permission",
    ];
    for (const kind of kinds) {
      expect(STALE_ON[kind], `no keys go stale on "${kind}"`).toBeTruthy();
      expect(STALE_ON[kind].length, `no keys go stale on "${kind}"`).toBeGreaterThan(0);
    }
    // And nothing extra: an entry for a kind the server never sends is a line nobody will ever
    // notice is dead.
    expect(Object.keys(STALE_ON).sort()).toEqual([...kinds].sort());
  });
});
