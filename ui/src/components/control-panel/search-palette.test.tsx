/**
 * The ⌘K palette as a person drives it: type, arrow down, Enter; Tab through kinds; ⇧↵ to filter
 * the page instead; a typo answered with "did you mean". The ranking itself is `search.test.ts` —
 * this is the keyboard and what each key ends up calling.
 */
import { fireEvent, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { BrainSnapshot } from "@/lib/backend/brain";
import { keys } from "@/lib/query-keys";
import { render, testQueryClient } from "@/test/render";
import { SearchPalette, type PaletteProps } from "./search-palette";
import { ago, whatHappened } from "./panel-states";

const snap = {
  counts: {},
  projects: [
    {
      id: 35,
      name: "Ironline Haulage — operations portal",
      description: "A freight operator's staff portal.",
      status: "archived",
      directory: "/Users/someone/Kith/ironline-haulage",
      created_at: "2026-09-30T08:00:00Z",
      updated_at: "2026-09-30T09:00:00Z",
      tasks_planning: 0,
      tasks_approved: 0,
      tasks_working: 0,
      tasks_done: 0,
      tasks_dropped: 0,
      tasks_active: 0,
      tasks_total: 0,
    },
  ],
  memories: [
    {
      id: 114,
      content: "Ironline Haulage (#35) sign-in direction, settled: light and clean.",
      tags: ["design", "ironline"],
      importance: 7,
      level: "recall",
      created_at: "2026-09-30T09:17:51Z",
    },
  ],
  journal: [{ id: 709, entry: "Third attempt at the Ironline sign-in.", created_at: "2026-09-30T09:17:42Z" }],
  tasks: [
    {
      id: 213,
      goal: "Nuclear effects: blast, thermal and fireball",
      status: "working",
      priority: "high",
      description: "",
      created_by: "kith",
      project_id: null,
      created_at: "2026-09-20T00:00:00Z",
      updated_at: "2026-09-29T00:00:00Z",
    },
  ],
  reminders: [],
  messages: [],
  sources: [],
  schedules: [],
} as unknown as BrainSnapshot;

function open(overrides: Partial<PaletteProps> = {}) {
  const props: PaletteProps = {
    open: true,
    onOpenChange: vi.fn(),
    snap,
    seed: "",
    onFilter: vi.fn(),
    onOpenTab: vi.fn(),
    onOpenProject: vi.fn(),
    onOpenTask: vi.fn(),
    ...overrides,
  };
  // The three side lists come from the cache, as they would after the sidebar and Files page.
  const client = testQueryClient();
  client.setQueryData(keys.conversations(500, ""), []);
  client.setQueryData(keys.workspace("."), []);
  render(
    <MemoryRouter>
      <SearchPalette {...props} />
    </MemoryRouter>,
    { client },
  );
  const input = screen.getByRole("combobox");
  return { props, input };
}

beforeEach(() => {
  // Nothing here should reach a server; anything that tries gets a polite no.
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 404 }));
});

describe("SearchPalette", () => {
  it("groups what it finds, selects the first, and Enter opens it", () => {
    const { props, input } = open();
    fireEvent.change(input, { target: { value: "ironline" } });
    const list = screen.getByRole("listbox", { name: "Results" });
    expect(within(list).getAllByRole("group").map((g) => g.getAttribute("aria-label"))).toEqual(["Projects", "Memory", "Journal"]);
    expect(within(list).getAllByRole("option")[0]).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(input, { key: "Enter" });
    expect(props.onOpenProject).toHaveBeenCalledWith(35);
    expect(props.onOpenChange).toHaveBeenCalledWith(false);
  });

  it("moves with the arrows, and opens a memory as its page filtered to the search", () => {
    const { props, input } = open();
    fireEvent.change(input, { target: { value: "ironline" } });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(screen.getByRole("article", { name: "Open in Memory" })).toBeInTheDocument();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(props.onOpenTab).toHaveBeenCalledWith("memory", "ironline");
  });

  it("Tab moves through the kinds, and the chip narrows the list", () => {
    const { input } = open();
    fireEvent.change(input, { target: { value: "ironline" } });
    fireEvent.keyDown(input, { key: "Tab" });
    expect(screen.getByRole("button", { name: /^Projects/ })).toHaveAttribute("aria-pressed", "true");
    fireEvent.keyDown(input, { key: "Tab" });
    expect(screen.getByRole("button", { name: /^Memory/ })).toHaveAttribute("aria-pressed", "true");
    expect(within(screen.getByRole("listbox")).getAllByRole("group").map((g) => g.getAttribute("aria-label"))).toEqual(["Memory"]);
  });

  it("⇧↵ filters the page behind instead of opening anything", () => {
    const { props, input } = open();
    fireEvent.change(input, { target: { value: "ironline" } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    expect(props.onFilter).toHaveBeenCalledWith("ironline");
    expect(props.onOpenProject).not.toHaveBeenCalled();
  });

  it("answers nothing with did-you-mean, which Enter takes", () => {
    const { input } = open();
    fireEvent.change(input, { target: { value: "nuclaer" } });
    expect(screen.getByText("Nothing matches ‘nuclaer’")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /nuclear/ })).toBeInTheDocument();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(input).toHaveValue("nuclear");
    expect(screen.getAllByRole("option")).toHaveLength(1);
  });

  it("offers to search everything when a kind found nothing", () => {
    const { input } = open();
    fireEvent.change(input, { target: { value: "nuclear" } });
    fireEvent.click(screen.getByRole("button", { name: /^Journal/ }));
    fireEvent.click(screen.getByRole("button", { name: /Search everything instead/ }));
    expect(screen.getByRole("button", { name: /^All/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getAllByRole("option")).toHaveLength(1);
  });

  it("starts from the page's filter when one is set", () => {
    const { input } = open({ seed: "blast" });
    expect(input).toHaveValue("blast");
  });
});

describe("panel states", () => {
  it("says what a failed /api/brain means, in words", () => {
    const now = Date.parse("2026-10-01T10:00:00Z");
    expect(whatHappened(new TypeError("Failed to fetch"), now - 130_000, now)).toBe(
      "The Kith server stopped responding 2 minutes ago.",
    );
    // A gateway status is the dev proxy saying the server behind it is gone — the same fact.
    expect(whatHappened(new Error("/api/brain 502"), now, now)).toBe("The Kith server stopped responding just now.");
    expect(whatHappened(new Error("/api/brain 500"), now - 60_000, now)).toBe(
      "The Kith server answered with an error (500) a minute ago.",
    );
  });

  it("counts time the way a person says it", () => {
    const now = 10_000_000;
    expect(ago(now - 10_000, now)).toBe("just now");
    expect(ago(now - 5 * 60_000, now)).toBe("5 minutes ago");
    expect(ago(now - 3 * 3_600_000, now)).toBe("3 hours ago");
  });
});
