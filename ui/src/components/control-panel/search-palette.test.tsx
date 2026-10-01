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
import { useFileViewer } from "@/lib/files";
import { render, testQueryClient, waitFor } from "@/test/render";
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

  // Awaited since 2026-10-01: under All, "nothing matches" now waits for his folder's search to
  // answer (here a 404, as an older server gives) rather than being said before it has looked.
  it("answers nothing with did-you-mean, which Enter takes", async () => {
    const { input } = open();
    fireEvent.change(input, { target: { value: "nuclaer" } });
    expect(await screen.findByText("Nothing matches ‘nuclaer’")).toBeInTheDocument();
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

describe("SearchPalette — his folder", () => {
  /** `fetch` that answers the folder search with `answer`, and everything else with a 404. */
  function serve(answer: (query: string) => Response | Promise<Response>) {
    const asked: { query: string; signal: AbortSignal | null | undefined }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = new URL(String(input), "http://kith.test");
      if (url.pathname !== "/api/workspace/search") return Promise.resolve(new Response("{}", { status: 404 }));
      const query = url.searchParams.get("q") ?? "";
      asked.push({ query, signal: init?.signal });
      return Promise.resolve(answer(query));
    });
    return asked;
  }
  const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  const hit = (over: Record<string, unknown>) => ({
    path: "src/retry.py",
    name: "retry.py",
    kind: "content",
    type: "file",
    size: 120,
    modified: 1790761163,
    ...over,
  });

  it("shows a line inside a file with its number and the match bold, and Enter opens that file", async () => {
    serve(() => json({ results: [hit({ line: 41, text: "def retry_with_backoff(attempt):" })], truncated: false }));
    const { props, input } = open();
    fireEvent.change(input, { target: { value: "backoff" } });
    const row = await screen.findByRole("option", { name: /retry\.py/ });
    expect(within(row).getByText("41")).toBeInTheDocument();
    expect(within(row).getByText("backoff").tagName).toBe("B");
    // The preview shows the line again, and says where it was found.
    expect(screen.getByText(/Found inside the file, on line 41/)).toBeInTheDocument();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(useFileViewer.getState().path).toBe("src/retry.py");
    expect(props.onOpenChange).toHaveBeenCalledWith(false);
  });

  it("puts a file found by name above a line found inside one", async () => {
    serve(() =>
      json({
        results: [hit({ path: "backoff-plan.md", name: "backoff-plan.md", kind: "name" }), hit({ line: 3, text: "backoff = 2" })],
        truncated: false,
      }),
    );
    const { input } = open();
    fireEvent.change(input, { target: { value: "backoff" } });
    await screen.findByRole("option", { name: /retry\.py/ });
    const names = screen.getAllByRole("option").map((o) => o.textContent ?? "");
    expect(names[0]).toContain("backoff-plan.md");
    expect(names[1]).toContain("retry.py");
  });

  it("asks once typing pauses, not once a letter", async () => {
    const asked = serve(() => json({ results: [], truncated: false }));
    // A blank seed, so the palette does not reopen on the last test's search and ask for that.
    const { input } = open({ seed: " " });
    for (const value of ["b", "ba", "bac", "back"]) fireEvent.change(input, { target: { value } });
    await waitFor(() => expect(asked.map((a) => a.query)).toEqual(["back"]));
  });

  it("cancels a search the next one has made moot", async () => {
    const asked = serve((query) => (query === "back" ? new Promise<Response>(() => {}) : json({ results: [], truncated: false })));
    const { input } = open({ seed: " " });
    fireEvent.change(input, { target: { value: "back" } });
    await waitFor(() => expect(asked).toHaveLength(1));
    fireEvent.change(input, { target: { value: "backoff" } });
    await waitFor(() => expect(asked).toHaveLength(2));
    expect(asked[0].signal?.aborted).toBe(true);
  });

  it("falls back to names it already had when the server has no search, and says so", async () => {
    const asked = serve(() => new Response("{}", { status: 404 }));
    const client = testQueryClient();
    client.setQueryData(keys.conversations(500, ""), []);
    client.setQueryData(keys.workspace("."), [{ name: "backoff-notes.md", type: "file", size: 10, modified: 0 }]);
    render(
      <MemoryRouter>
        <SearchPalette open onOpenChange={vi.fn()} snap={snap} seed="" onFilter={vi.fn()} onOpenTab={vi.fn()} onOpenProject={vi.fn()} onOpenTask={vi.fn()} />
      </MemoryRouter>,
      { client },
    );
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "backoff" } });
    await waitFor(() => expect(asked).toHaveLength(1));
    expect(await screen.findByRole("option", { name: /backoff-notes\.md/ })).toBeInTheDocument();
    expect(await screen.findByText(/not by what’s inside them/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
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
