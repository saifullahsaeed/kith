/**
 * The board, over the conversation that produced it.
 *
 * Opened from a tool result in the thread, because that is where the designs arrive. The task it
 * belongs to is looked up here rather than passed in — a deliverable result carries its
 * `task_id` and nothing else, and every other fact the board needs (which files, what they are
 * called, which project anchors their paths) is one request away.
 *
 * Covering the pane rather than splitting it: a board is for comparing six things at 34%, and a
 * 400px column would make that the same unreadable strip the editor already was.
 */
import { X } from "lucide-react";
import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";

import { DesignBoardPage } from "@/components/design/design-board-page";
import { fetchTaskDetail } from "@/lib/backend/brain";
import { useBoardOverlay } from "@/lib/design/board-overlay";
import { looksLikeADesign } from "@/lib/design/detect";

export function BoardOverlay() {
  const open = useBoardOverlay((state) => state.open);
  const hide = useBoardOverlay((state) => state.hide);

  useEffect(() => {
    if (!open) return;
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") hide();
    };
    addEventListener("keydown", escape);
    return () => removeEventListener("keydown", escape);
  }, [open, hide]);

  const task = useQuery({
    queryKey: ["task", open?.taskId],
    queryFn: () => fetchTaskDetail(open!.taskId),
    enabled: !!open,
  });

  if (!open) return null;

  const designs = (task.data?.deliverables ?? [])
    .filter((one) => looksLikeADesign(one.kind, one.content))
    .map((one) => ({ file: one.content, title: one.title }));

  return (
    <div className="absolute inset-0 z-40 flex flex-col bg-background">
      <div className="flex shrink-0 items-center gap-3 border-b border-border/70 px-4 py-2">
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{task.data?.goal ?? open.title ?? "Designs"}</div>
          <div className="truncate text-xs text-muted-foreground">
            {designs.length} design{designs.length === 1 ? "" : "s"} · Esc to close
          </div>
        </div>
        <button
          type="button"
          onClick={hide}
          aria-label="Close the board"
          className="flex size-8 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        >
          <X className="size-4" />
        </button>
      </div>

      <div className="min-h-0 flex-1">
        <DesignBoardPage
          taskId={open.taskId}
          projectId={task.data?.project_id ?? open.projectId}
          designs={designs}
          onBack={hide}
        />
      </div>
    </div>
  );
}
