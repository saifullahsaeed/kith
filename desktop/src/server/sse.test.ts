/**
 * The stream, read a byte at a time.
 *
 * The browser gets this for free — `EventSource` *is* the parser — so this code exists only because
 * the shell holds the stream in its main process, where there is nothing to use. Which makes it the
 * one piece of the event pipeline with no standard implementation behind it, and incremental parsing
 * is exactly the sort of code that handles every frame you try by hand and then fails on the one
 * that arrives split across two TCP reads. So: the split cases first, because they are the ones no
 * amount of looking at the code catches.
 */
import { describe, expect, it, vi } from "vitest";

import { reader, splitId, type ParsedEvent } from "./sse";

/** Feed a stream in chunks of `size` and collect what came out. */
function feed(text: string, size = text.length): ParsedEvent[] {
  const out: ParsedEvent[] = [];
  const read = reader((event) => out.push(event));
  for (let at = 0; at < text.length; at += size) read(text.slice(at, at + size));
  return out;
}

const frame = (id: string, type: string, data: unknown) =>
  `id: ${id}\nevent: ${type}\ndata: ${JSON.stringify(data)}\n\n`;

describe("reading frames", () => {
  it("reads a whole frame", () => {
    const [event] = feed(frame("run-1-7", "changed", { kind: "task" }));
    expect(event).toEqual({
      id: 7,
      epoch: "run-1",
      type: "changed",
      data: { kind: "task" },
      raw: "run-1-7",
    });
  });

  it("holds a frame that arrives in pieces until it is terminated", () => {
    // One byte at a time is the worst case a socket can hand us, and the cheapest way to be sure
    // nothing depends on a chunk boundary lining up with anything.
    const events = feed(frame("run-1-1", "changed", { kind: "task" }), 1);
    expect(events).toHaveLength(1);
    expect(events[0]!.data).toEqual({ kind: "task" });
  });

  it("does not emit a frame that is still incomplete", () => {
    const out: ParsedEvent[] = [];
    const read = reader((event) => out.push(event));
    read("id: run-1-1\nevent: changed\ndata: {\"kind\":\"task\"}");
    expect(out).toEqual([]); // no blank line yet
    read("\n\n");
    expect(out).toHaveLength(1);
  });

  it("reads several frames out of one chunk", () => {
    const events = feed(
      frame("run-1-1", "changed", { kind: "task" }) + frame("run-1-2", "activity", { text: "hi" }),
    );
    expect(events.map((one) => one.type)).toEqual(["changed", "activity"]);
  });

  it("reads frames that straddle a chunk boundary in the middle of the batch", () => {
    // The case that only shows up in production: two frames in flight, the split landing inside
    // the second one's `data:` line.
    const stream =
      frame("run-1-1", "changed", { kind: "task" }) + frame("run-1-2", "changed", { kind: "project" });
    const cut = stream.length - 12;
    const out: ParsedEvent[] = [];
    const read = reader((event) => out.push(event));
    read(stream.slice(0, cut));
    read(stream.slice(cut));
    expect(out.map((one) => (one.data as { kind: string }).kind)).toEqual(["task", "project"]);
  });
});

describe("what is not an event", () => {
  it("skips the keep-alive", () => {
    // `: ping` every fifteen seconds is how a dead connection becomes noticeable. It is a comment,
    // and a comment that arrived as an event would be a refetch every fifteen seconds forever.
    expect(feed(": ping\n\n: open\n\n")).toEqual([]);
  });

  it("passes the keep-alive without losing the frame after it", () => {
    const events = feed(": ping\n\n" + frame("run-1-3", "changed", { kind: "task" }));
    expect(events).toHaveLength(1);
    expect(events[0]!.id).toBe(3);
  });

  it("drops a frame whose data is not JSON without dropping the stream", () => {
    /* Reconnecting on a bad frame would replay it and fail on it again, which is a loop. The next
     * event prompts the same refetch anyway, so the frame is what gets dropped, not the connection. */
    const events = feed(
      "id: run-1-1\nevent: changed\ndata: {not json\n\n" +
        frame("run-1-2", "changed", { kind: "task" }),
    );
    expect(events.map((one) => one.id)).toEqual([2]);
  });

  it("ignores a field with no colon rather than mangling it", () => {
    // Per the spec that is a field with an empty value. Splitting on an index of -1 would have
    // turned `id` into `i`, which is the shape of a stream that half works.
    const events = feed("id\nevent: changed\ndata: {\"kind\":\"task\"}\n\n");
    expect(events[0]!.raw).toBe("");
    expect(events[0]!.type).toBe("changed");
  });

  it("joins repeated data lines, which the spec allows", () => {
    const events = feed('event: changed\ndata: {"kind":\ndata: "task"}\n\n');
    expect(events[0]!.data).toEqual({ kind: "task" });
  });
});

describe("the id", () => {
  it("splits epoch from position", () => {
    expect(splitId("7c7ae5d4-42")).toEqual({ epoch: "7c7ae5d4", n: 42 });
  });

  it("reads a bare number as having no epoch", () => {
    // What a server too old to name its run sends, and what its fallback expects back.
    expect(splitId("42")).toEqual({ epoch: "", n: 42 });
  });

  it("keeps an epoch that contains a dash", () => {
    // Split on the *last* dash, so a run named "run-2" is not read as epoch "run" at position NaN.
    expect(splitId("run-2-9")).toEqual({ epoch: "run-2", n: 9 });
  });

  it("reads an unparseable position as zero rather than NaN", () => {
    // NaN would propagate into the cursor and make every comparison against it false, so the
    // client would never notice a gap again.
    expect(splitId("run-1-x")).toEqual({ epoch: "run-1", n: 0 });
  });

  it("carries the raw id through untouched", () => {
    // It is what goes back as `Last-Event-ID`. Rebuilding it from the halves would be a second
    // place for the format to live.
    const [event] = feed(frame("abc-def-99", "changed", {}));
    expect(event!.raw).toBe("abc-def-99");
    expect(event!.epoch).toBe("abc-def");
  });
});

describe("delivery", () => {
  it("calls back once per event, in order", () => {
    const seen = vi.fn();
    const read = reader(seen);
    read(frame("run-1-1", "changed", { kind: "task" }));
    read(frame("run-1-2", "activity", { text: "hi" }));
    expect(seen).toHaveBeenCalledTimes(2);
    expect(seen.mock.calls.map(([event]) => event.id)).toEqual([1, 2]);
  });
});
