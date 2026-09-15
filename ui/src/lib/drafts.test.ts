/**
 * Text you typed and did not send outlives the pane you typed it in.
 *
 * The pane is unmounted by switching to another tab — `layout-view` renders the active tab and an
 * `EmptyPane` for the rest — and the composer's text lived in the runtime that pane owned. So the
 * message was gone, with nothing said about it. These are the cases that mechanism has to get
 * right, and two of them are the reason the key is the tab and not the conversation.
 *
 * The re-render guard is tested by identity rather than by counting renders: `useDraftedTabs`
 * projects the store to ids and compares shallowly, so typing must not change what the tab strip
 * and the conversations list are handed. Asserting on the projection is the honest version of
 * that claim — a render count would pass on a store that happened not to be subscribed.
 */

import { beforeEach, describe, expect, it } from "vitest";

import { alreadyGoing, draftFor, useDrafts, type Draft } from "./drafts";

/** A draft with nothing in it but the words, which is the ordinary case. */
function typed(text: string, over: Partial<Draft> = {}): Draft {
  return { conversationId: "", text, attachments: [], projectId: null, ...over };
}

/** What `useDraftedTabs` hands the strip and the list, without a React render to get it. */
function held(): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [uid, draft] of Object.entries(useDrafts.getState().drafts)) {
    out[uid] = draft.conversationId;
  }
  return out;
}

beforeEach(() => {
  useDrafts.setState({ drafts: {} });
});

describe("text you typed and did not send", () => {
  it("is still there after the pane it was typed in is gone", () => {
    useDrafts.getState().hold("tab-1", typed("half a thought"));
    // The pane unmounting is not an event this store hears — that is the point of it being here.
    expect(draftFor("tab-1")?.text).toBe("half a thought");
  });

  it("is kept per tab, so one conversation open twice does not overwrite itself", () => {
    useDrafts.getState().hold("tab-1", typed("what I said in the left pane", { conversationId: "c-7" }));
    useDrafts.getState().hold("tab-2", typed("and in the right one", { conversationId: "c-7" }));
    expect(draftFor("tab-1")?.text).toBe("what I said in the left pane");
    expect(draftFor("tab-2")?.text).toBe("and in the right one");
  });

  it("survives the chat it is in being named, and answers to the same tab after", () => {
    // The half of the design that keying on the conversation could not do: a chat with no id yet
    // is `""`, so every unnamed chat would share one slot and the next new chat would open
    // holding this text.
    useDrafts.getState().hold("tab-1", typed("before it had a name"));
    useDrafts.getState().hold("tab-1", typed("before it had a name", { conversationId: "c-9" }));
    expect(Object.keys(useDrafts.getState().drafts)).toEqual(["tab-1"]);
    expect(draftFor("tab-1")?.conversationId).toBe("c-9");
  });

  it("is dropped when the composer goes empty, which is what sending is", () => {
    useDrafts.getState().hold("tab-1", typed("about to be sent"));
    useDrafts.getState().hold("tab-1", typed(""));
    expect(draftFor("tab-1")).toBeUndefined();
  });

  it("does not count a stray space as something you were writing", () => {
    useDrafts.getState().hold("tab-1", typed("   \n  "));
    expect(draftFor("tab-1")).toBeUndefined();
  });

  it("counts a pasted file even with no words beside it", () => {
    // A paste over 2,000 characters is lifted out of the text into an attachment by `paste.ts`, so
    // a draft can be entirely attachment — and losing that silently while restoring nothing is
    // the trap this closes.
    const log = new File(["a stack trace"], "pasted-1.txt", { type: "text/plain" });
    useDrafts.getState().hold("tab-1", typed("", { attachments: [log] }));
    expect(draftFor("tab-1")?.attachments).toEqual([log]);
  });

  it("keeps a reply quote alongside the words it belongs to", () => {
    useDrafts
      .getState()
      .hold("tab-1", typed("this bit", { quote: { text: "the line I clicked", messageId: "m-3" } }));
    expect(draftFor("tab-1")?.quote?.messageId).toBe("m-3");
  });

  it("does not count a reply quote with no words beside it", () => {
    /* Steering with Enter empties the box and leaves the quote behind — `setText("")` does not
     * touch it. Counting a lone quote therefore left a permanent "unsent text" mark on a chat
     * where everything typed had been delivered, and restoring it put a stale excerpt on the next
     * real message. A quote alone is not sendable either, which settles it. */
    useDrafts
      .getState()
      .hold("tab-1", typed("", { quote: { text: "the line I clicked", messageId: "m-3" } }));
    expect(draftFor("tab-1")).toBeUndefined();
  });

  it("keeps the project a chat was started in", () => {
    // Restored words with the project gone would send the first turn — the one that says what the
    // work is — against no project at all.
    useDrafts.getState().hold("tab-1", typed("about this repo", { projectId: 4 }));
    expect(draftFor("tab-1")?.projectId).toBe(4);
  });

  it("stops being held when its tab is closed", () => {
    useDrafts.getState().hold("tab-1", typed("in a tab about to close"));
    useDrafts.getState().hold("tab-2", typed("in one that stays"));
    useDrafts.getState().keep(new Set(["tab-2"]));
    expect(draftFor("tab-1")).toBeUndefined();
    expect(draftFor("tab-2")?.text).toBe("in one that stays");
  });

  it("is nothing at all for a tab with no permanent id", () => {
    // A layout stored before `uid` existed has tabs without one. Holding under `""` would make
    // every such tab share a draft; the pane holds nothing instead.
    expect(draftFor("")).toBeUndefined();
  });
});

describe("what the strip and the conversations list are handed", () => {
  it("does not change while you keep typing", () => {
    useDrafts.getState().hold("tab-1", typed("a", { conversationId: "c-1" }));
    const before = held();
    useDrafts.getState().hold("tab-1", typed("ab", { conversationId: "c-1" }));
    // The whole reason the projection exists: the store moved, this did not.
    expect(held()).toEqual(before);
  });

  it("changes when a chat starts holding one", () => {
    expect(held()).toEqual({});
    useDrafts.getState().hold("tab-1", typed("something", { conversationId: "c-1" }));
    expect(held()).toEqual({ "tab-1": "c-1" });
  });

  it("changes when the chat it belongs to is finally named", () => {
    useDrafts.getState().hold("tab-1", typed("said before it had a name"));
    expect(held()).toEqual({ "tab-1": "" });
    useDrafts.getState().hold("tab-1", typed("said before it had a name", { conversationId: "c-9" }));
    expect(held()).toEqual({ "tab-1": "c-9" });
  });

  it("names no conversation for a chat that has none", () => {
    useDrafts.getState().hold("tab-1", typed("a brand new chat"));
    // `""` is filtered out by the caller building the set of marked rows: there is no row to mark.
    expect(Object.values(held()).filter(Boolean)).toEqual([]);
  });
});

describe("writes that are not writes", () => {
  it("leaves the store untouched when nothing said anything new", () => {
    useDrafts.getState().hold("tab-1", typed("steady", { conversationId: "c-1" }));
    const was = useDrafts.getState().drafts;
    // A composer notifies for dictation and for an attachment finishing, not only for typing.
    useDrafts.getState().hold("tab-1", typed("steady", { conversationId: "c-1" }));
    expect(useDrafts.getState().drafts).toBe(was);
  });

  it("leaves it untouched when an empty composer reports again", () => {
    const was = useDrafts.getState().drafts;
    useDrafts.getState().hold("tab-1", typed(""));
    expect(useDrafts.getState().drafts).toBe(was);
  });

  it("leaves it untouched when a prune finds nothing stale", () => {
    useDrafts.getState().hold("tab-1", typed("here"));
    const was = useDrafts.getState().drafts;
    useDrafts.getState().keep(new Set(["tab-1"]));
    expect(useDrafts.getState().drafts).toBe(was);
  });

  it("drops a quote-only entry rather than leaving the previous one standing", () => {
    // The order the steer path actually produces: words with a quote, then the words gone.
    const quote = { text: "the line I clicked", messageId: "m-3" };
    useDrafts.getState().hold("tab-1", typed("this bit is wrong", { quote }));
    useDrafts.getState().hold("tab-1", typed("", { quote }));
    expect(draftFor("tab-1")).toBeUndefined();
  });

  it("notices an attachment arriving even though the words did not change", () => {
    useDrafts.getState().hold("tab-1", typed("see attached"));
    const log = new File(["x"], "pasted-1.txt");
    useDrafts.getState().hold("tab-1", typed("see attached", { attachments: [log] }));
    expect(draftFor("tab-1")?.attachments).toEqual([log]);
  });
});

describe("a message already on its way out", () => {
  /* The state `send()` leaves behind for the length of an attachment upload: text blanked, sending
   * flag up, attachments still on the composer. Held as a draft it marks the message you just
   * sent as unsent — and a tab switch inside that window strands the mark and re-attaches the
   * delivered files. */
  it("is not a draft, however full the composer still looks", () => {
    expect(alreadyGoing({ isEmpty: false, canSend: false })).toBe(true);
  });

  it("does not catch an ordinary half-written message", () => {
    expect(alreadyGoing({ isEmpty: false, canSend: true })).toBe(false);
  });

  it("does not catch a composer that has gone empty, so sending still clears the draft", () => {
    // The notification after the upload settles: `isEmpty` is true again, so this must fall
    // through to `hold`, which is the one path that drops the entry.
    expect(alreadyGoing({ isEmpty: true, canSend: false })).toBe(false);
  });

  it("does not catch a draft typed while a turn is running", () => {
    // `isSendDisabled` is a permanent false on this runtime, so a running turn leaves `canSend`
    // true — which is what keeps mid-turn typing held rather than deleted.
    expect(alreadyGoing({ isEmpty: false, canSend: true })).toBe(false);
  });
});
