/**
 * The board shows because it is open, not because of where you happen to be looking.
 *
 * It first shipped mounted inside `ChatPane` and gated on that pane being the focused one, which
 * turned "does the board appear" into a question about tab bookkeeping — and the answer was no
 * about half the time. Two ways it failed, both from the same mistake:
 *
 * * switching tabs *unmounts* a pane, so a board opened in one died the moment you looked at
 *   another, with the store still saying it was open;
 * * the store is global, so with two chats open the surviving pane would have drawn the other
 *   conversation's board over its own.
 *
 * A board is keyed by a task and belongs to the window. These assert the property that follows
 * from that: what decides whether it draws is the store and nothing else.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BoardOverlay } from "@/components/design/board-overlay";
import { useBoardOverlay } from "@/lib/design/board-overlay";
import { fetchTaskDetail, fetchWorkspaceFile } from "@/lib/backend/brain";
import { render, screen, waitFor } from "@/test/render";

vi.mock("@/lib/backend/brain", () => ({
  fetchTaskDetail: vi.fn(),
  fetchWorkspaceFile: vi.fn(),
  saveWorkspaceFile: vi.fn(),
  fetchDesignSystem: vi.fn(),
  // The overlay listens for what he puts in front of you — see `use-showing`.
  fetchShowing: vi.fn(async () => null),
  takeShowing: vi.fn(async () => {}),
}));

const task = vi.mocked(fetchTaskDetail);
const file = vi.mocked(fetchWorkspaceFile);

beforeEach(() => {
  task.mockReset();
  file.mockReset();
  file.mockResolvedValue({ path: "", content: "" });
  useBoardOverlay.getState().hide();
});

describe("what decides whether the board is on screen", () => {
  it("draws nothing at all until one is opened", () => {
    const { container } = render(<BoardOverlay />);
    expect(container.firstChild).toBeNull();
  });

  it("draws as soon as the store says a board is open", async () => {
    task.mockResolvedValue({
      id: 174,
      goal: "Engagements table",
      project_id: 3,
      deliverables: [{ id: 1, kind: "file", title: "A", content: "screens/a.html" }],
    } as never);

    render(<BoardOverlay />);
    useBoardOverlay.getState().show({ taskId: 174, projectId: 3, title: "A" });

    expect(await screen.findByText("Engagements table")).toBeInTheDocument();
  });

  it("needs no conversation, no pane and no focus", async () => {
    // The regression this file exists for. Nothing here is a chat, and the board still draws.
    task.mockResolvedValue({ id: 9, goal: "Loose board", project_id: null, deliverables: [] } as never);

    render(<BoardOverlay />);
    useBoardOverlay.getState().show({ taskId: 9, projectId: null });

    expect(await screen.findByText("Loose board")).toBeInTheDocument();
  });

  it("goes away when it is closed, and can be opened again", async () => {
    task.mockResolvedValue({ id: 9, goal: "Loose board", project_id: null, deliverables: [] } as never);
    const { container } = render(<BoardOverlay />);

    useBoardOverlay.getState().show({ taskId: 9, projectId: null });
    await screen.findByText("Loose board");

    useBoardOverlay.getState().hide();
    await waitFor(() => expect(container.firstChild).toBeNull());

    useBoardOverlay.getState().show({ taskId: 9, projectId: null });
    expect(await screen.findByText("Loose board")).toBeInTheDocument();
  });

  it("does not wait on the task when the project is already known", async () => {
    // A deliverable carries its project now, so a board opened from the thread can anchor the
    // file it writes before anything else answers — see migration v47.
    task.mockReturnValue(new Promise(() => {}) as never);

    render(<BoardOverlay />);
    useBoardOverlay.getState().show({ taskId: 5, projectId: 3, title: "Known" });

    expect(await screen.findByText("Known")).toBeInTheDocument();
  });
});
