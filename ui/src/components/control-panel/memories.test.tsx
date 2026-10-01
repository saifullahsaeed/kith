/**
 * The Memory screen as the 2026-10-01 redesign draws it: a list to scan, the selected memory in
 * full beside it, the copies flagged and mergeable, and the keys the footer promises.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/ui/confirm";
import type { BrainSnapshot, Memory } from "@/lib/backend/brain";
import { Memories } from "./memories";

vi.mock("@/lib/backend/brain", async (original) => ({
  ...(await original<typeof import("@/lib/backend/brain")>()),
  deleteBrainItem: vi.fn(async () => undefined),
}));
const { deleteBrainItem } = await import("@/lib/backend/brain");

const CHECKPOINT =
  "Canonical checkpoint: active work is Task 73, the chat orchestration refactor. Provider completion extraction shipped in commit 82376bf and verification passed.";

const memory = (id: number, content: string, extra: Partial<Memory> = {}): Memory => ({
  id,
  content,
  tags: [],
  importance: 8,
  level: "core",
  created_at: `2026-08-10T0${id % 10}:00:00Z`,
  ...extra,
});

const MEMORIES = [
  memory(1, CHECKPOINT, { importance: 10, tags: ["checkpoint", "task-73"] }),
  memory(2, CHECKPOINT.replace("Canonical", "Current"), { importance: 9, tags: ["checkpoint"] }),
  memory(3, `${CHECKPOINT} Website Task 83 is still open.`, { importance: 7, tags: ["checkpoint"] }),
  memory(4, "He wants the market answer verified from primary sources, not comparison articles.", {
    importance: 6,
    tags: ["preferences"],
  }),
  memory(5, "The full backend suite takes about 7.5 minutes; run it in the background.", { level: "recall" }),
];

function mount(memories = MEMORIES) {
  const handlers = {
    refresh: vi.fn(),
    remove: vi.fn(async () => true),
    relevel: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
  };
  const page = (list: Memory[]) => (
    <ConfirmProvider>
      <Memories snap={{ memories: list } as unknown as BrainSnapshot} query="" {...handlers} />
    </ConfirmProvider>
  );
  const view = render(page(memories));
  return { ...handlers, replace: (list: Memory[]) => view.rerender(page(list)) };
}

const selectedId = () => screen.getByRole("option", { selected: true }).getAttribute("data-memory");

describe("the list", () => {
  it("opens on front of mind, with real counts for each level", () => {
    mount();
    const lens = screen.getByRole("group", { name: "Which memories" });
    expect(within(lens).getByRole("button", { name: /Front of mind/ })).toHaveAttribute("aria-pressed", "true");
    expect(within(lens).getByRole("button", { name: /Front of mind/ })).toHaveTextContent("4");
    expect(within(lens).getByRole("button", { name: /Recall/ })).toHaveTextContent("1");
    expect(within(lens).getByRole("button", { name: /All/ })).toHaveTextContent("5");
    expect(screen.getAllByRole("option")).toHaveLength(4);
  });

  it("sorts by importance, then newest — and by newest on asking", () => {
    mount();
    expect(screen.getAllByRole("option").map((row) => row.getAttribute("data-memory"))).toEqual(["1", "2", "3", "4"]);
    fireEvent.click(screen.getByRole("button", { name: /^Sorted by importance/ }));
    expect(screen.getAllByRole("option").map((row) => row.getAttribute("data-memory"))).toEqual(["4", "3", "2", "1"]);
  });

  it("narrows to a tag from the chips, which count what is in view", () => {
    mount();
    const tags = screen.getByRole("group", { name: "Filter by tag" });
    const chip = within(tags).getByRole("button", { name: /preferences/ });
    expect(chip).toHaveTextContent("1");
    fireEvent.click(chip);
    expect(screen.getAllByRole("option").map((row) => row.getAttribute("data-memory"))).toEqual(["4"]);
  });

  it("flags the copies, and says how many say the same thing", () => {
    mount();
    expect(screen.getByRole("status")).toHaveTextContent("3 of these say nearly the same thing");
    expect(screen.getByRole("status")).toHaveTextContent("he carries every copy into every turn");
    const similar = screen.getAllByRole("option").filter((row) => within(row).queryByText("similar"));
    expect(similar.map((row) => row.getAttribute("data-memory"))).toEqual(["1", "2", "3"]);
  });

  it("puts the notice away on Not now", () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    expect(screen.queryByRole("status")).toBeNull();
  });
});

describe("the keys in the footer", () => {
  it("moves with ↑↓, edits with E, forgets with F", () => {
    const { remove } = mount();
    expect(selectedId()).toBe("1");
    fireEvent.keyDown(window, { key: "ArrowDown" });
    fireEvent.keyDown(window, { key: "ArrowDown" });
    expect(selectedId()).toBe("3");
    fireEvent.keyDown(window, { key: "ArrowUp" });
    expect(selectedId()).toBe("2");

    fireEvent.keyDown(window, { key: "f" });
    expect(remove).toHaveBeenCalledWith("memory", 2, MEMORIES[1].content);

    fireEvent.keyDown(window, { key: "e" });
    const editor = screen.getByLabelText("Memory text");
    expect(editor).toHaveValue(MEMORIES[1].content);
    // Typing in the editor is typing, not shortcuts.
    fireEvent.keyDown(editor, { key: "ArrowDown" });
    expect(selectedId()).toBe("2");
  });
});

describe("the selection", () => {
  it("holds its place when the selected memory goes, and starts at the top of a new view", () => {
    const { replace } = mount();
    fireEvent.keyDown(window, { key: "ArrowDown" });
    expect(selectedId()).toBe("2");
    // Forgotten: the one that slides into its place is selected, not the top of the list.
    replace(MEMORIES.filter((m) => m.id !== 2));
    expect(selectedId()).toBe("3");

    fireEvent.click(within(screen.getByRole("group", { name: "Which memories" })).getByRole("button", { name: /Recall/ }));
    expect(selectedId()).toBe("5");
    expect(screen.getByText("1 of 1")).toBeTruthy();
  });
});

describe("the reader", () => {
  it("saves importance once the clicks stop", async () => {
    const { update } = mount();
    fireEvent.click(screen.getByRole("button", { name: "Lower importance" }));
    fireEvent.click(screen.getByRole("button", { name: "Lower importance" }));
    expect(update).not.toHaveBeenCalled();
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    expect(update).toHaveBeenCalledWith("memory", 1, { importance: 8 });
  });

  it("edits tags as a set: × takes one off, + tag puts one on", () => {
    const { update } = mount();
    fireEvent.click(screen.getByRole("button", { name: "Remove tag task-73" }));
    expect(update).toHaveBeenLastCalledWith("memory", 1, { tags: ["checkpoint"] });
    fireEvent.click(screen.getByRole("button", { name: "+ tag" }));
    const field = screen.getByLabelText("New tag");
    fireEvent.change(field, { target: { value: "#odoo" } });
    fireEvent.keyDown(field, { key: "Enter" });
    expect(update).toHaveBeenLastCalledWith("memory", 1, { tags: ["checkpoint", "odoo"] });
  });

  it("moves a memory between levels", () => {
    const { relevel } = mount();
    fireEvent.click(screen.getByRole("button", { name: "Move to recall" }));
    expect(relevel).toHaveBeenCalledWith(1, "recall");
  });

  it("lists what each copy says differently, and merges them into this one after asking", async () => {
    const { refresh } = mount();
    expect(screen.getByText("Nearly the same as 2 others")).toBeTruthy();
    expect(screen.getByText("Says “Current” where this says “Canonical”")).toBeTruthy();
    expect(screen.getByText(/^Only #3 says something this one doesn't/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Merge all 3 into this one" }));
    const ask = await screen.findByRole("dialog");
    expect(ask).toHaveTextContent("Forget the other 2 copies?");
    fireEvent.click(within(ask).getByRole("button", { name: "Forget 2" }));
    await waitFor(() => expect(refresh).toHaveBeenCalled());
    expect(vi.mocked(deleteBrainItem).mock.calls.map((call) => call[1]).sort()).toEqual([2, 3]);
  });
});
