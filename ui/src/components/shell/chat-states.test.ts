import { describe, expect, it } from "vitest";

import type { ActivityItem } from "@/lib/backend/activity";

import {
  chatState,
  clock,
  permissionHolder,
  presenceFor,
  runningWorkflows,
  type ChatFacts,
} from "./chat-states";

function line(run: string, state: string, text = "", conversation = "c1"): ActivityItem {
  return {
    kind: "workflow",
    text,
    at: "2026-09-26T00:00:00Z",
    conversation,
    workflow: { run, state: state as NonNullable<ActivityItem["workflow"]>["state"], name: "devices-screen" },
  };
}

function facts(over: Partial<ChatFacts> = {}): ChatFacts {
  return { live: new Set(), waiting: new Set(), asking: null, workflows: new Map(), ...over };
}

describe("which workflows are running", () => {
  it("takes the board's running runs, keyed by their conversation", () => {
    const got = runningWorkflows(
      [
        { run: "wf-1", conversation: "c1", name: "devices-screen", phase: "Survey", state: "running" },
        { run: "wf-2", conversation: "c2", state: "finished" },
      ],
      [],
    );
    expect([...got.keys()]).toEqual(["c1"]);
    expect(got.get("c1")?.phase).toBe("Survey");
  });

  it("shows a run the feed has seen start before the board has been re-read", () => {
    const got = runningWorkflows([], [line("wf-1", "started"), line("wf-1", "phase", "Build")]);
    expect(got.get("c1")).toMatchObject({ run: "wf-1", phase: "Build", name: "devices-screen" });
  });

  it("does not bring a finished run back to life from an older feed line", () => {
    const got = runningWorkflows(
      [{ run: "wf-1", conversation: "c1", state: "finished" }],
      [line("wf-1", "started"), line("wf-1", "phase", "Survey")],
    );
    expect(got.size).toBe(0);
  });

  it("drops a run the feed has seen end", () => {
    const got = runningWorkflows(
      [{ run: "wf-1", conversation: "c1", state: "running" }],
      [line("wf-1", "stopped")],
    );
    expect(got.size).toBe(0);
  });
});

describe("whose permission request it is", () => {
  it("is the one live chat not already waiting on a question", () => {
    expect(permissionHolder(1, ["a", "b"], new Set(["a"]))).toBe("b");
  });

  it("is nobody's when two parked turns could have asked", () => {
    expect(permissionHolder(1, ["a", "b"], new Set())).toBeNull();
  });

  it("is nobody's when nothing is pending", () => {
    expect(permissionHolder(0, ["a"], new Set())).toBeNull();
  });
});

describe("a chat's state", () => {
  const flows = new Map([["c1", { run: "wf-1", conversation: "c1", name: "devices-screen", phase: "Survey" }]]);

  it("ranks needing you over working over a workflow", () => {
    expect(chatState("c1", facts({ waiting: new Set(["c1"]), live: new Set(["c1"]), workflows: flows }))?.tag).toBe("Asking");
    expect(chatState("c1", facts({ asking: "c1", live: new Set(["c1"]) }))?.tag).toBe("Needs yes");
    expect(chatState("c1", facts({ live: new Set(["c1"]), workflows: flows }))?.tone).toBe("working");
    expect(chatState("c1", facts({ workflows: flows }))).toMatchObject({ tone: "workflow", tag: "Survey" });
  });

  it("has nothing to say about a draft or an idle chat", () => {
    expect(chatState("", facts({ live: new Set([""]) }))).toBeUndefined();
    expect(chatState("c9", facts())).toBeUndefined();
  });
});

describe("the presence pill", () => {
  it("speaks for the chat in front of you first", () => {
    expect(presenceFor("c1", facts({ waiting: new Set(["c1", "c2"]) }), undefined)).toEqual({
      kind: "waiting",
      chats: 2,
      target: "",
    });
    expect(presenceFor("c1", facts({ live: new Set(["c1"]), waiting: new Set(["c2"]) }), 5)).toEqual({
      kind: "working",
      since: 5,
    });
  });

  it("counts workflows ahead of a question elsewhere, and points at where they run", () => {
    const flows = new Map([["c2", { run: "wf-1", conversation: "c2", name: "x", phase: "" }]]);
    expect(presenceFor("c1", facts({ workflows: flows, waiting: new Set(["c3"]) }), undefined)).toEqual({
      kind: "workflow",
      count: 1,
      target: "c2",
    });
  });

  it("says he is waiting on you when a chat elsewhere is asking, and takes you there", () => {
    expect(presenceFor("c1", facts({ waiting: new Set(["c3"]) }), undefined)).toEqual({
      kind: "waiting",
      chats: 1,
      target: "c3",
    });
  });

  it("is simply here otherwise", () => {
    expect(presenceFor("c1", facts(), undefined)).toEqual({ kind: "here" });
  });
});

describe("the clock", () => {
  it("reads like a stopwatch", () => {
    expect(clock(68_000)).toBe("1:08");
    expect(clock(4_000)).toBe("0:04");
    expect(clock(3_723_000)).toBe("1:02:03");
    expect(clock(-5)).toBe("0:00");
  });
});
