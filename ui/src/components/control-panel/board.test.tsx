/**
 * The board's keyboard: select a card, ← → moves it one column through the same update a drag
 * makes, and Working refuses from the keys exactly as it refuses a drop.
 */
import { describe, expect, it, vi } from "vitest";

import type { Task } from "@/lib/backend/brain";
import { fireEvent, render, screen, waitFor } from "@/test/render";
import { Board } from "./board";

const task = (over: Partial<Task>): Task => ({
  id: 1,
  goal: "A task",
  status: "planning",
  priority: "normal",
  description: "",
  created_by: "kith",
  project_id: 29,
  created_at: "2026-09-22T10:00:00Z",
  updated_at: "2026-09-22T10:00:00Z",
  ...over,
});

const TASKS = [
  task({ id: 207, goal: "The screen answers the question", status: "planning" }),
  task({ id: 215, goal: "Destruction physics", status: "working", checklist_done: 7, checklist_total: 7 }),
  task({ id: 220, goal: "Approved and waiting", status: "approved" }),
  task({ id: 210, goal: "Bombing run", status: "done" }),
];

function mount(update = vi.fn(async () => {}), onOpenTask = vi.fn()) {
  render(<Board tasks={TASKS} projectId={29} create={vi.fn()} update={update} onOpenTask={onOpenTask} />);
  return { update, onOpenTask };
}

const card = (id: number) => screen.getAllByRole("button").find((one) => one.hasAttribute("data-card") && one.textContent?.includes(`#${id}`))!;
const laneOf = (id: number) => card(id).closest("section")?.getAttribute("aria-label");

describe("Board keyboard", () => {
  it("moves a selected card one column with the arrow keys, through the drag's update", async () => {
    const { update } = mount();
    fireEvent.click(card(215), { detail: 1 });
    fireEvent.keyDown(card(215), { key: "ArrowLeft" });
    expect(update).toHaveBeenCalledWith("task", 215, { status: "approved" });
    // Shown where it is going before the server answers — and the keyboard goes with it.
    expect(laneOf(215)).toBe("Up next");
    await waitFor(() => expect(document.activeElement).toBe(card(215)));
  });

  it("approves a plan with →", () => {
    const { update } = mount();
    fireEvent.click(card(207), { detail: 1 });
    fireEvent.keyDown(card(207), { key: "ArrowRight" });
    expect(update).toHaveBeenCalledWith("task", 207, { status: "approved" });
  });

  it("refuses to put a card into Working, and says so", () => {
    const { update } = mount();
    fireEvent.click(card(220), { detail: 1 });
    fireEvent.keyDown(card(220), { key: "ArrowRight" });
    fireEvent.click(card(210), { detail: 1 });
    fireEvent.keyDown(card(210), { key: "ArrowLeft" });
    expect(update).not.toHaveBeenCalled();
    expect(screen.getByText("Only he moves a card into Working")).toBeInTheDocument();
  });

  it("puts a card back when the move fails", async () => {
    const { update } = mount(vi.fn(async () => Promise.reject(new Error("offline"))));
    fireEvent.click(card(215), { detail: 1 });
    fireEvent.keyDown(card(215), { key: "ArrowRight" });
    expect(update).toHaveBeenCalledWith("task", 215, { status: "done" });
    await waitFor(() => expect(laneOf(215)).toBe("Working"));
    expect(screen.getByText(/didn't go through/)).toBeInTheDocument();
  });

  it("still moves by drag, and Working still takes no drop", () => {
    const { update } = mount();
    const data = { setData: vi.fn(), getData: () => "220", effectAllowed: "", dropEffect: "" };
    const column = (name: string) => screen.getByRole("region", { name });
    fireEvent.dragStart(card(220).closest("article")!, { dataTransfer: data });
    fireEvent.dragOver(column("Working"), { dataTransfer: data });
    fireEvent.drop(column("Working"), { dataTransfer: data });
    expect(update).not.toHaveBeenCalled();
    fireEvent.dragStart(card(220).closest("article")!, { dataTransfer: data });
    fireEvent.drop(column("Planning"), { dataTransfer: data });
    expect(update).toHaveBeenCalledWith("task", 220, { status: "planning" });
  });

  it("selects on the first click and opens on the second, or on Enter", () => {
    const { onOpenTask } = mount();
    fireEvent.pointerDown(card(215));
    fireEvent.click(card(215), { detail: 1 });
    expect(onOpenTask).not.toHaveBeenCalled();
    expect(card(215)).toHaveAttribute("aria-current", "true");
    fireEvent.pointerDown(card(215));
    fireEvent.click(card(215), { detail: 1 });
    expect(onOpenTask).toHaveBeenCalledWith(215);
    fireEvent.keyDown(card(210), { key: "Enter" });
    expect(onOpenTask).toHaveBeenCalledWith(210);
  });
});
