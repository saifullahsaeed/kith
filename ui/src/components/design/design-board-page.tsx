/**
 * A task's board: the designs on it, where they sit, and the way into one of them.
 *
 * Everything the board shows is derived except the arrangement. The frames are the task's HTML
 * deliverables, so attaching a design puts it on the board and nothing has to be told; only the
 * positions and the notes are stored, in one small file, and losing that file costs an
 * arrangement rather than any work.
 *
 * The board and the editor are one surface with two depths — the board is where you compare, the
 * editor is where you change — so opening one from the other keeps the task page's state and
 * comes straight back.
 */
import { ArrowLeft, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";

import { type BoardDesign, DesignBoard } from "@/components/design/design-board";
import { DesignPage } from "@/components/design/design-page";
import { Button } from "@/components/ui/button";
import { fetchWorkspaceFile, saveWorkspaceFile } from "@/lib/backend/brain";
import { type Board, boardPath, EMPTY, readBoard, writeBoard } from "@/lib/design/board";

export interface DesignBoardPageProps {
  taskId: number;
  projectId: number | null;
  /** The task's design deliverables, already filtered by `looksLikeADesign`. */
  designs: { file: string; title: string }[];
  onBack: () => void;
}

export function DesignBoardPage({ taskId, projectId, designs, onBack }: DesignBoardPageProps) {
  const [open, setOpen] = useState<string | null>(null);
  /** The arrangement as it is being changed. Null until somebody moves something — until then
   *  the stored file is the answer and there is nothing local to hold. */
  const [edited, setEdited] = useState<Board | null>(null);
  const saving = useRef<ReturnType<typeof setTimeout> | null>(null);

  const path = boardPath(taskId);

  const stored = useQuery({
    queryKey: ["board", path, projectId],
    // A board that has never been arranged has no file, and that is the ordinary case rather
    // than an error — the derived layout is the answer.
    queryFn: () => fetchWorkspaceFile(path, projectId).catch(() => ({ path, content: "" })),
  });

  const files = useQueries({
    queries: designs.map((one) => ({
      queryKey: ["design", one.file, projectId],
      queryFn: () => fetchWorkspaceFile(one.file, projectId),
    })),
  });

  // Derived during render rather than copied into state by an effect: an effect here would
  // render once with no board, once more to fill it in, and would need its own rule about not
  // overwriting an arrangement in progress. The `??` is that rule.
  const board: Board | null = edited ?? (stored.data ? readBoard(stored.data.content) : null);

  /* Written on settle rather than on every change.
   *
   * Dragging a frame is a change per pointer move, and a board is arranged by dragging. Writing
   * each one would be a hundred file writes to move one card — and each write is a checkpoint,
   * so it would also bury his history under the act of tidying up. */
  const change = useCallback(
    (next: Board) => {
      setEdited(next);
      if (saving.current) clearTimeout(saving.current);
      saving.current = setTimeout(() => {
        void saveWorkspaceFile(path, writeBoard(next), projectId).catch(() => {
          // An arrangement that failed to save is not worth interrupting anyone over; it is
          // recoverable by moving the thing again, and the designs themselves are untouched.
        });
      }, 800);
    },
    [path, projectId],
  );

  useEffect(() => () => void (saving.current && clearTimeout(saving.current)), []);

  if (open) {
    const design = designs.find((one) => one.file === open);
    return (
      <DesignPage
        path={open}
        title={design?.title}
        projectId={projectId}
        onBack={() => setOpen(null)}
      />
    );
  }

  if (stored.isLoading || board === null) {
    return (
      <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
        <RefreshCw className="size-4 animate-spin" />
        Opening the board…
      </div>
    );
  }

  const loaded: BoardDesign[] = designs.map((one, at) => ({
    file: one.file,
    title: one.title,
    html: files[at]?.data?.content ?? "",
  }));

  return (
    <div className="flex h-full flex-col">
      <header className="flex shrink-0 items-center gap-3 border-b border-border/70 px-6 py-3">
        <Button variant="ghost" size="sm" onClick={onBack} className="gap-1.5">
          <ArrowLeft className="size-4" />
          Back
        </Button>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">Designs</div>
          <div className="truncate text-xs text-muted-foreground">
            {designs.length} on this board · double-click a frame to edit it
          </div>
        </div>
      </header>

      <div className="min-h-0 flex-1">
        <DesignBoard designs={loaded} board={board} onChange={change} onOpen={setOpen} />
      </div>
    </div>
  );
}

export { EMPTY as EMPTY_BOARD };
