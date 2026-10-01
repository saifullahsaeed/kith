/**
 * Lifetime reads all the way back, a page at a time, against a fake of
 * `GET /api/brain/timeline?page=1&before=…` that pages the way the server does.
 *
 * It used to be one request for the newest 500 things and a footnote saying that was as far as it
 * went. What is pinned here: the cursor is the last event's own time, the strip's thirty days are
 * paged in before anything asks, a date older than what is loaded pages back to it, the chips count
 * the server's totals, the end of history says so, and a server from before paging is one page.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { BrainSnapshot, TimelineEvent } from "@/lib/backend/brain";
import { dayKey, longDate, msOf } from "./day-format";
import { Lifetime } from "./lifetime";
import { useTimelinePages } from "./timeline-pages";

vi.stubEnv("TZ", "Asia/Riyadh");

const HOUR = 3_600_000;
const SNAP = { tasks: [], projects: [], messages: [] } as unknown as BrainSnapshot;

/** `n` journal entries, one an hour, newest an hour ago — ~46 days for 1,100. */
function history(n: number): TimelineEvent[] {
  const newest = Math.floor(Date.now() / HOUR) * HOUR - HOUR;
  return Array.from({ length: n }, (_, i) => ({
    kind: "journal" as const,
    at: new Date(newest - i * HOUR).toISOString(),
    text: `Entry ${i}`,
    meta: { id: n - i },
  }));
}

let asked: URL[] = [];

/** The server's paging, over `all` (newest first): strictly older than `before`, 500 a page. */
function serve(all: TimelineEvent[], { bare = false } = {}) {
  asked = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = new URL(String(input), "http://kith.test");
    asked.push(url);
    if (bare) return Response.json(all.slice(0, 500));
    const before = url.searchParams.get("before");
    const older = before ? all.filter((event) => msOf(event.at) < msOf(before)) : all;
    return Response.json({
      events: older.slice(0, 500),
      has_more: older.length > 500,
      totals: { journal: all.length, memory: 0, task: 0, reminder: 0, message: 0 },
      message_kinds: {},
      oldest: all[all.length - 1]?.at ?? null,
    });
  });
}

function Harness() {
  const { events, paging } = useTimelinePages();
  return (
    <main>
      <Lifetime snap={SNAP} events={events} paging={paging} onOpenTask={vi.fn()} onNavigate={vi.fn()} />
    </main>
  );
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Harness />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const footer = () => screen.getAllByRole("status").at(-1)!;

beforeEach(() => {
  asked = [];
});

describe("Lifetime paging", () => {
  it("pages back by the last event's own time until the strip's thirty days are loaded, and no further", async () => {
    const all = history(1100);
    serve(all);
    mount();

    // Page one reaches ~21 days back, short of the strip; page two reaches ~42, past it.
    await waitFor(() => expect(asked).toHaveLength(2));
    expect(asked[0]!.searchParams.get("page")).toBe("1");
    expect(asked[0]!.searchParams.has("before")).toBe(false);
    expect(asked[1]!.searchParams.get("before")).toBe(all[499]!.at);
    await waitFor(() => expect(within(footer()).getByRole("button", { name: "Load older" })).toBeTruthy());
    expect(asked).toHaveLength(2);
  });

  it("counts the chips from the server's totals, not from what has loaded", async () => {
    serve(history(1100));
    mount();
    await waitFor(() => expect(asked).toHaveLength(2));

    // 1,000 loaded, 1,100 in all — the chip says the second.
    expect(screen.getByRole("button", { name: /^Journal/ }).textContent).toContain("1,100");
  });

  it("folds a day that a page edge cut in two into one row once both pages are in", async () => {
    const all = history(1100);
    serve(all);
    mount();
    await waitFor(() => expect(asked).toHaveLength(2));

    // The 500th and 501st entries are an hour apart either side of the edge; find their day.
    const edge = msOf(all[499]!.at);
    const day = screen.getByRole("region", { name: longDate(edge) });
    const sameDay = all.filter((event) => dayKey(msOf(event.at)) === dayKey(edge)).length;
    expect(sameDay).toBe(24);
    expect(within(day).getAllByText(/^Wrote \d+ journal entries$/).map((row) => row.textContent)).toEqual(["Wrote 24 journal entries"]);
  });

  it("loads older on request and says where history begins", async () => {
    const all = history(1100);
    serve(all);
    mount();
    const more = await within(await waitFor(() => footer())).findByRole("button", { name: "Load older" });

    fireEvent.click(more);

    await waitFor(() => expect(footer().textContent).toBe(`That's the beginning — the first thing he did was on ${longDate(msOf(all[1099]!.at))}.`));
    expect(asked).toHaveLength(3);
    expect(asked[2]!.searchParams.get("before")).toBe(all[999]!.at);
  });

  it("pages back to a date picked from Jump to date that is not loaded yet", async () => {
    const all = history(1100);
    serve(all);
    mount();
    await waitFor(() => expect(within(footer()).getByRole("button", { name: "Load older" })).toBeTruthy());

    // 44 days ago: older than both loaded pages, newer than his first day.
    const target = msOf(all[44 * 24]!.at);
    fireEvent.click(screen.getByRole("button", { name: "Jump to date" }));
    const label = new Date(target).toLocaleDateString(undefined, { month: "long" });
    for (let i = 0; i < 3 && !screen.queryByRole("button", { name: new RegExp(`^${new Date(target).getDate()} ${label}, not loaded yet`) }); i++) {
      fireEvent.click(screen.getByRole("button", { name: "Previous month" }));
    }
    fireEvent.click(screen.getByRole("button", { name: new RegExp(`^${new Date(target).getDate()} ${label}, not loaded yet`) }));

    await waitFor(() => expect(asked).toHaveLength(3));
    await waitFor(() => expect(screen.getByRole("region", { name: longDate(target) })).toBeTruthy());
    expect(screen.getByRole("button", { name: "Jump to date" })).toBeTruthy();
  });

  it("takes a server from before paging as the one and only page", async () => {
    serve(history(40), { bare: true });
    mount();

    // Its newest 500 are not necessarily his first day, so the end does not claim to be.
    await waitFor(() => expect(footer().textContent).toBe("That's as far back as this goes."));
    expect(asked).toHaveLength(1);
    // No totals from that server, so the chip counts what it has.
    expect(screen.getByRole("button", { name: /^Journal/ }).textContent).toContain("40");
  });
});
