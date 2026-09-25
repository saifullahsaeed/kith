/**
 * The canvas in a reply — the place the retired ```html canvas used to occupy.
 *
 * It reads the turn's own tool calls: designs he attached (`add_deliverable`) and designs he put
 * in front of you (`show_canvas`, or `show_board` in an older transcript). The second one is the
 * point of this file: a reply that says "have a look" about designs made yesterday must show
 * them, not only the reply that happened to attach the files.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DesignsInTurn } from "@/components/design/designs-in-turn";
import { useBoardOverlay } from "@/lib/design/board-overlay";
import { render, screen } from "@/test/render";

let parts: unknown[] = [];

vi.mock("@assistant-ui/react", async (original) => ({
  ...(await original<typeof import("@assistant-ui/react")>()),
  useAuiState: (select: (state: unknown) => unknown) => select({ message: { parts } }),
}));

vi.mock("@/lib/backend/brain", () => ({
  fetchWorkspaceFile: vi.fn(async () => ({ path: "", content: "" })),
  fetchDesignSystem: vi.fn(async () => null),
}));

const call = (toolName: string, args: unknown, result: unknown) => ({
  type: "tool-call",
  toolName,
  args,
  result: { ok: true, result },
});

const shown = (name = "show_canvas") =>
  call(name, { id: 7 }, {
    shown: true,
    task: "Engagements table",
    project_id: 3,
    designs: [
      { title: "A — comfortable", file: ".kith/screens/a.html" },
      { title: "B — compact", file: ".kith/screens/b.html" },
    ],
  });

beforeEach(() => {
  parts = [];
  useBoardOverlay.getState().hide();
});

describe("the canvas in a reply", () => {
  it("is not there when the turn touched no designs", () => {
    parts = [{ type: "text", text: "morning" }];
    const { container } = render(<DesignsInTurn />);
    expect(container.firstChild).toBeNull();
  });

  it("appears for designs he showed, not only ones he attached", () => {
    parts = [shown()];
    render(<DesignsInTurn />);
    expect(screen.getByText(/Canvas · 2 designs/)).toBeInTheDocument();
    expect(screen.getByText("A — comfortable")).toBeInTheDocument();
  });

  it("still reads the old tool name from an older transcript", () => {
    parts = [shown("show_board")];
    render(<DesignsInTurn />);
    expect(screen.getByText(/Canvas · 2 designs/)).toBeInTheDocument();
  });

  it("counts a design attached and then shown once", () => {
    parts = [
      call("add_deliverable", { id: 7 }, {
        kind: "file",
        title: "A — comfortable",
        content: ".kith/screens/a.html",
        task_id: 7,
        project_id: 3,
      }),
      shown(),
    ];
    render(<DesignsInTurn />);
    expect(screen.getByText(/Canvas · 2 designs/)).toBeInTheDocument();
  });

  it("ignores a show that found nothing", () => {
    parts = [call("show_canvas", { id: 7 }, { shown: false, why: "no design files" })];
    const { container } = render(<DesignsInTurn />);
    expect(container.firstChild).toBeNull();
  });

  it("opens the task's canvas, anchored to its project", () => {
    parts = [shown()];
    render(<DesignsInTurn />);
    screen.getByRole("button", { name: "Open canvas" }).click();
    expect(useBoardOverlay.getState().open).toMatchObject({ taskId: 7, projectId: 3 });
  });
});
