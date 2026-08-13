import { describe, expect, it } from "vitest";

import { groupAlerts, readAlert, type Alert } from "./alerts";
import type { KithMessage } from "./backend/messages";

/** The real sentences, taken from the templates in server/kith/tools/tasks.py. Inventing
 *  approximations here would test the regex against itself. */
const SENTENCES = {
  note: "New note on “Restore CI compatibility after the domain import migration” (task #79): Pushed b432a34 to origin/main successfully.",
  asked:
    "I need your input on “Restore CI compatibility” (task #79): which remote should this target?",
  failed:
    "I couldn't finish “Restore CI compatibility” (task #79): the checkpoint never went green.",
  handedBack:
    "I've left “Restore CI compatibility” (task #79) with you — I couldn't get past it on my own.",
  delivered: "Finished something on “Restore CI compatibility”: the compatibility checkpoint",
  bare: "I need your say-so before I can read /Users/x/.kith/work/task-99.md.",
};

let next = 1;
function message(body: string, over: Partial<KithMessage> = {}): KithMessage {
  return {
    id: next++,
    body,
    read: 1,
    sender: "kith",
    kind: "note",
    created_at: "2026-08-12T08:54:00+00:00",
    link: "/tasks/79",
    ...over,
  };
}

describe("readAlert", () => {
  it.each(Object.entries(SENTENCES).filter(([name]) => name !== "bare"))(
    "lifts the subject off a %s",
    (_name, body) => {
      const alert = readAlert(message(body));
      expect(alert.subject).toMatch(/^Restore CI compatibility/);
      expect(alert.body).not.toContain("Restore CI compatibility");
      expect(alert.body.length).toBeGreaterThan(0);
    },
  );

  it("keeps the part you have not read, whole", () => {
    expect(readAlert(message(SENTENCES.note)).body).toBe(
      "Pushed b432a34 to origin/main successfully.",
    );
  });

  it("leaves a sentence that has no subject clause alone", () => {
    const alert = readAlert(message(SENTENCES.bare, { kind: "asked", link: "/chat/3" }));
    expect(alert.subject).toBeNull();
    expect(alert.body).toBe(SENTENCES.bare);
  });

  it("never renders an empty row when the lead-in is the whole message", () => {
    const alert = readAlert(message("Finished something on “A task”:"));
    expect(alert.body).toBe("Finished something on “A task”:");
    expect(alert.subject).toBeNull();
  });

  it("takes the task number from the link rather than the prose", () => {
    expect(readAlert(message(SENTENCES.note)).taskId).toBe(79);
    expect(readAlert(message(SENTENCES.bare, { link: "/chat/3" })).taskId).toBeNull();
  });
});

const alerts = (...bodies: [string, Partial<KithMessage>?][]): Alert[] =>
  bodies.map(([body, over]) => readAlert(message(body, over)));

const notes = (kind: string) => kind !== "asked" && kind !== "stuck";
const groupable = (alert: Alert) => notes(alert.kind);

describe("groupAlerts", () => {
  it("folds a run about the same thing into one thread", () => {
    const threads = groupAlerts(
      alerts([SENTENCES.note], ["New note on “Restore CI compatibility…” (task #79): and again"]),
      groupable,
    );
    // Two different titles above, so only a real match folds — this one must not.
    expect(threads).toHaveLength(2);

    const same = groupAlerts(
      alerts([SENTENCES.note], [SENTENCES.note], [SENTENCES.note]),
      groupable,
    );
    expect(same).toHaveLength(1);
    expect(same[0].items).toHaveLength(3);
    expect(same[0].subject).toBe("Restore CI compatibility after the domain import migration");
  });

  it("never buries something that is waiting on you", () => {
    const threads = groupAlerts(
      alerts([SENTENCES.note], [SENTENCES.asked, { kind: "asked" }], [SENTENCES.note]),
      groupable,
    );
    expect(threads).toHaveLength(3);
    expect(threads[1].kind).toBe("asked");
  });

  it("only folds neighbours, so the timeline keeps its order", () => {
    const threads = groupAlerts(
      alerts(
        [SENTENCES.note],
        ["New note on “Another task” (task #80): elsewhere", { link: "/tasks/80" }],
        [SENTENCES.note],
      ),
      groupable,
    );
    expect(threads.map((one) => one.items.length)).toEqual([1, 1, 1]);
  });

  it("does not fold messages that have no subject to share", () => {
    const threads = groupAlerts(alerts([SENTENCES.bare], [SENTENCES.bare]), groupable);
    expect(threads).toHaveLength(2);
  });

  it("carries the unread mark up to the thread", () => {
    const items = [
      readAlert(message(SENTENCES.note), false),
      readAlert(message(SENTENCES.note), true),
    ];
    expect(groupAlerts(items, groupable)[0].unread).toBe(true);
  });
});
