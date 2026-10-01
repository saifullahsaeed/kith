/**
 * The Reminders screen against the shapes the server takes: `{note, in_minutes}` or `{note, at}`
 * to set one, `{status: "cancelled"}` to cancel, `{at}` to snooze and `{note, at}` to edit.
 */
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/ui/confirm";
import type { BrainSnapshot, Reminder } from "@/lib/backend/brain";
import { Reminders } from "./reminders";

const MIN = 60_000;
const iso = (offsetMinutes: number) => new Date(Date.now() + offsetMinutes * MIN).toISOString();

function reminder(id: number, fireIn: number, setBefore: number, status: string, note: string): Reminder {
  return { id, note, status, fire_at: iso(fireIn), created_at: iso(fireIn - setBefore), fires: "" } as Reminder;
}

function snapshot(reminders: Reminder[]): BrainSnapshot {
  return {
    counts: {},
    memories: [],
    journal: [],
    tasks: [],
    reminders,
    messages: [],
    sources: [],
    schedules: [],
    projects: [],
  };
}

const DAY = 24 * 60;
const REMINDERS = [
  reminder(1, 30, 5, "pending", "Check the Blastwave build and report"),
  reminder(2, -2 * DAY, 5, "done", "Re-check the full backend pytest run for Task #74"),
  reminder(3, -2 * DAY - 10, 5, "done", "Re-check the backend pytest run for Task #74 again"),
  reminder(4, -5 * DAY, 8, "done", "Alarm — 9:30 AM"),
];

function setup(reminders = REMINDERS) {
  const create = vi.fn(() => Promise.resolve());
  const update = vi.fn(() => Promise.resolve());
  const remove = vi.fn(() => Promise.resolve(true));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ConfirmProvider>
          <Reminders
            snap={snapshot(reminders)}
            query=""
            refresh={() => {}}
            remove={remove}
            relevel={() => {}}
            create={create}
            update={update}
          />
        </ConfirmProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { create, update, remove };
}

beforeEach(() => {
  // The chat titles come from the history list; none are needed here.
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(new Response(JSON.stringify({ conversations: [], storage: {} })))),
  );
});
afterEach(() => vi.unstubAllGlobals());

describe("the Reminders screen", () => {
  it("counts only what is still to fire, and counts it down", () => {
    setup();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Reminders1 upcoming");
    const row = screen.getByRole("article", { name: "Check the Blastwave build and report" });
    expect(row).toHaveTextContent(/in (29|30) min/);
  });

  it("folds Past by day, newest open, one open at a time", () => {
    setup();
    const past = screen.getByRole("region", { name: "Past" });
    // The day headers — told apart from the menus inside a day, which also say aria-expanded.
    const days = () => within(past).getAllByRole("button").filter((button) => /reminders? ·/.test(button.textContent ?? ""));
    expect(days()).toHaveLength(2);
    expect(days().map((day) => day.getAttribute("aria-expanded"))).toEqual(["true", "false"]);
    expect(days()[0]).toHaveTextContent("2 reminders · all fired");
    fireEvent.click(days()[1]);
    expect(days().map((day) => day.getAttribute("aria-expanded"))).toEqual(["false", "true"]);
    expect(days()[1]).toHaveTextContent("1 reminder · fired");
  });

  it("sets a reminder in 30 minutes as minutes, and tomorrow morning as an instant", async () => {
    const { create } = setup();
    const box = screen.getByLabelText("Remind him");
    fireEvent.change(box, { target: { value: "Look at the deploy" } });
    expect(screen.getByRole("status")).toHaveTextContent(/Fires today at .* — in 30 minutes|Fires tomorrow at/);
    fireEvent.click(screen.getByRole("button", { name: /Set reminder/ }));
    await waitFor(() => expect(create).toHaveBeenCalledWith("reminder", { note: "Look at the deploy", in_minutes: 30 }));

    fireEvent.change(box, { target: { value: "Morning check" } });
    fireEvent.click(screen.getByRole("button", { name: "Tomorrow 9:00" }));
    fireEvent.click(screen.getByRole("button", { name: /Set reminder/ }));
    const tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 1);
    tomorrow.setHours(9, 0, 0, 0);
    await waitFor(() => expect(create).toHaveBeenLastCalledWith("reminder", { note: "Morning check", at: tomorrow.toISOString() }));
  });

  it("asks before cancelling, then cancels by status", async () => {
    const { update } = setup();
    const row = screen.getByRole("article", { name: "Check the Blastwave build and report" });
    fireEvent.click(within(row).getByRole("button", { name: "Cancel" }));
    fireEvent.click(await screen.findByRole("button", { name: "Cancel reminder" }));
    await waitFor(() => expect(update).toHaveBeenCalledWith("reminder", 1, { status: "cancelled" }));
  });

  it("snoozes from the keyboard to half an hour after its own time", async () => {
    const { update } = setup();
    fireEvent.keyDown(window, { key: "ArrowDown" });
    fireEvent.keyDown(window, { key: "s" });
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    const [, id, body] = update.mock.calls[0] as unknown as [string, number, { at: string }];
    expect(id).toBe(1);
    const moved = (new Date(body.at).getTime() - new Date(REMINDERS[0].fire_at).getTime()) / MIN;
    expect(Math.round(moved)).toBe(30);
  });

  it("edits the words and the time in place", async () => {
    const { update } = setup();
    const row = screen.getByRole("article", { name: "Check the Blastwave build and report" });
    fireEvent.click(within(row).getByRole("button", { name: "Edit" }));
    const form = screen.getByRole("form", { name: "Edit reminder" });
    fireEvent.change(within(form).getByLabelText("What he should come back to"), { target: { value: "Check it twice" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    const [, id, body] = update.mock.calls[0] as unknown as [string, number, { note: string; at: string }];
    expect(id).toBe(1);
    expect(body.note).toBe("Check it twice");
    // Kept to the minute: the picker holds a day and an HH:MM.
    const fire = new Date(REMINDERS[0].fire_at);
    fire.setSeconds(0, 0);
    expect(body.at).toBe(fire.toISOString());
  });

  it("says so in place when the server refuses an edit", async () => {
    const { update } = setup();
    update.mockImplementationOnce(() => Promise.reject(new Error("update reminder failed")));
    fireEvent.keyDown(window, { key: "ArrowDown" });
    await act(async () => {
      fireEvent.keyDown(window, { key: "s" });
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn’t snooze it");
  });

  it("shows the patterns behind the reminders", () => {
    setup();
    const card = screen.getByRole("region", { name: "They keep repeating" });
    expect(card).toHaveTextContent("2of 3");
    expect(card).toHaveTextContent("Re-check a running pytest suite");
  });

  it("says what to do when nothing is waiting", () => {
    setup(REMINDERS.slice(1));
    expect(screen.getByText("Nothing is waiting to fire")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("none upcoming");
  });
});
