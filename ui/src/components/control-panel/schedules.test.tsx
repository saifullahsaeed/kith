/**
 * The Schedules screen against the shapes the server takes: `{note, daily_at: "HH:MM"}` or
 * `{note, every_minutes}` to add, `{status}` to pause and resume. "Weekdays" has no server shape
 * yet, so it must not be choosable.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/ui/confirm";
import type { BrainSnapshot, Reminder, Schedule } from "@/lib/backend/brain";
import { Schedules } from "./schedules";

vi.stubEnv("TZ", "Asia/Riyadh");

/** Six of his real reminders: four pytest re-checks and two GitHub Actions checks. */
const REAL = [
  { id: 1, status: "done", fire_at: "2026-08-06T04:48:05.270908+00:00", created_at: "2026-08-06T04:43:05.270930+00:00", conversation_id: null,
    note: "Check GitHub Actions deploy runs for Sadeef/sadeef-ai on main, especially run 31071981307 for commit 3dcc3d7, and report the result or inspect failure logs." },
  { id: 2, status: "done", fire_at: "2026-08-06T06:01:02.022444+00:00", created_at: "2026-08-06T05:56:02.022491+00:00", conversation_id: null,
    note: "Check GitHub Actions deploy run 31075555985 for Sadeef/sadeef-ai (commit b432a34) and report the final status on task #79." },
  { id: 3, status: "done", fire_at: "2026-08-06T10:06:01.065388+00:00", created_at: "2026-08-06T10:01:01.065418+00:00", conversation_id: null,
    note: "Check the full backend pytest run for Task #74 after the runtime_context extraction, then report the result before any commit." },
  { id: 4, status: "done", fire_at: "2026-08-06T10:12:54.428940+00:00", created_at: "2026-08-06T10:07:54.429006+00:00", conversation_id: null,
    note: "Re-check the full backend pytest run for Task #74; report the final result before any commit." },
  { id: 5, status: "done", fire_at: "2026-08-06T10:19:46.738753+00:00", created_at: "2026-08-06T10:14:46.738777+00:00", conversation_id: null,
    note: "Re-check the full backend pytest run for Task #74, report the final pass/fail result before any commit." },
  { id: 6, status: "done", fire_at: "2026-08-06T11:13:20.530246+00:00", created_at: "2026-08-06T11:08:20.530278+00:00", conversation_id: null,
    note: "Check the in-progress full backend pytest run for Task #74. If it passes, review current diff, commit the agent runtime extraction, and run/monitor CI. If it fails, fix the failure first." },
] as unknown as Reminder[];

function snapshot(schedules: Schedule[] = [], reminders: Reminder[] = REAL): BrainSnapshot {
  return {
    counts: {},
    memories: [],
    journal: [],
    tasks: [],
    reminders,
    messages: [],
    sources: [],
    schedules,
    projects: [],
  };
}

function job(over: Partial<Schedule> = {}): Schedule {
  return {
    id: 7,
    note: "Check GitHub Actions on Sadeef/sadeef-ai and report anything red",
    conversation_id: null,
    every_minutes: null,
    daily_at: "09:00",
    next_fire: new Date(Date.now() + 60 * 60_000).toISOString(),
    last_fired: null,
    status: "active",
    created_at: new Date().toISOString(),
    fires: "",
    ...over,
  };
}

function setup(snap = snapshot()) {
  const create = vi.fn(() => Promise.resolve());
  const update = vi.fn(() => Promise.resolve());
  const remove = vi.fn(() => Promise.resolve(true));
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <ConfirmProvider>
          <Schedules snap={snap} query="" refresh={() => {}} remove={remove} relevel={() => {}} create={create} update={update} />
        </ConfirmProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { create, update, remove };
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(new Response(JSON.stringify({ conversations: [], storage: {} })))),
  );
  try {
    window.localStorage.clear();
  } catch {
    // no storage in this environment
  }
});
afterEach(() => vi.unstubAllGlobals());

describe("the Schedules screen", () => {
  it("adds a daily job as daily_at, and an interval as every_minutes", async () => {
    const { create } = setup();
    fireEvent.change(screen.getByLabelText("Standing job"), { target: { value: "Brief me on my sources" } });
    fireEvent.change(screen.getByLabelText("Time"), { target: { value: "7:30" } });
    fireEvent.click(screen.getByRole("button", { name: /Add schedule/ }));
    await waitFor(() => expect(create).toHaveBeenCalledWith("schedule", { note: "Brief me on my sources", daily_at: "07:30" }));

    fireEvent.change(screen.getByLabelText("Standing job"), { target: { value: "Check the queue" } });
    fireEvent.click(screen.getByRole("button", { name: "Every N minutes" }));
    fireEvent.change(screen.getByLabelText("Minutes between runs"), { target: { value: "45" } });
    expect(screen.getByRole("status")).toHaveTextContent("Runs every 45 min");
    fireEvent.click(screen.getByRole("button", { name: /Add schedule/ }));
    await waitFor(() => expect(create).toHaveBeenLastCalledWith("schedule", { note: "Check the queue", every_minutes: 45 }));
  });

  it("shows Weekdays but will not let it be chosen", () => {
    setup();
    expect(screen.getByRole("button", { name: "Weekdays at" })).toBeDisabled();
  });

  it("says what to do when there are no jobs, and suggests them from his reminders", () => {
    setup();
    expect(screen.getByText("No standing jobs yet")).toBeInTheDocument();
    const suggestions = screen.getByRole("region", { name: /Suggested from what you keep asking him/ });
    expect(within(suggestions).getAllByRole("article").map((card) => card.getAttribute("aria-label"))).toEqual([
      "Run the full backend suite and report any failures",
      "Check GitHub Actions on Sadeef/sadeef-ai and report anything red",
    ]);
  });

  it("adds a suggestion as a daily job that posts where the reminders did", async () => {
    const fromChat = REAL.map((r) => (r.id === 2 ? { ...r, conversation_id: "chat-1" } : r));
    const { create } = setup(snapshot([], fromChat));
    const card = screen.getByRole("article", { name: "Check GitHub Actions on Sadeef/sadeef-ai and report anything red" });
    fireEvent.click(within(card).getByRole("button", { name: "Add" }));
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith("schedule", {
        note: "Check GitHub Actions on Sadeef/sadeef-ai and report anything red",
        daily_at: expect.stringMatching(/^\d{2}:\d{2}$/),
        conversation_id: "chat-1",
      }),
    );
  });

  it("retires a suggestion a job already covers, and remembers a dismissal", () => {
    setup(snapshot([job()]));
    const suggestions = screen.getByRole("region", { name: /Suggested from what you keep asking him/ });
    expect(within(suggestions).getAllByRole("article")).toHaveLength(1);
    fireEvent.click(within(suggestions).getByRole("button", { name: "Dismiss this suggestion" }));
    expect(within(suggestions).queryAllByRole("article")).toHaveLength(0);
    expect(within(suggestions).getByText("Nothing to suggest")).toBeInTheDocument();
  });

  it("pauses and resumes with the switch", async () => {
    const { update } = setup(snapshot([job()]));
    const toggle = screen.getByRole("switch", { name: "Pause this schedule" });
    expect(toggle).toHaveAttribute("aria-checked", "true");
    fireEvent.click(toggle);
    await waitFor(() => expect(update).toHaveBeenCalledWith("schedule", 7, { status: "paused" }));
  });

  it("is honest that a job tied to no chat will not run", () => {
    setup(snapshot([job()]));
    expect(screen.getByText("started on its first run")).toBeInTheDocument();
    expect(screen.getByText("Never run yet")).toBeInTheDocument();
  });
});
