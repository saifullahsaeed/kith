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
import {RefreshCw} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";

import { type BoardDesign, DesignBoard } from "@/components/design/design-board";
import { DesignPage } from "@/components/design/design-page";
import { fetchWorkspaceFile, saveWorkspaceFile } from "@/lib/backend/brain";
import { type Board, boardPath, EMPTY, readBoard, writeBoard } from "@/lib/design/board";

export interface DesignBoardPageProps {
  taskId: number;
  projectId: number | null;
  /** The task's design deliverables, already filtered by `looksLikeADesign`. */
  designs: { file: string; title: string }[];
  /** Somewhere to go back to, when there is one. The overlay has its own close and
   *  passes nothing; the task panel has no other way out and passes a handler. */
  onBack?: () => void;
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
  /** The arrangement as of the last change, for the flush on the way out. */
  const pending = useRef<Board | null>(null);

  const change = useCallback(
    (next: Board) => {
      setEdited(next);
      pending.current = next;
      if (saving.current) clearTimeout(saving.current);
      saving.current = setTimeout(() => {
        pending.current = null;
        void saveWorkspaceFile(path, writeBoard(next), projectId).catch(() => {
          // An arrangement that failed to save is not worth interrupting anyone over; it is
          // recoverable by moving the thing again, and the designs themselves are untouched.
        });
      }, 800);
    },
    [path, projectId],
  );

  /* Flushed on the way out, not merely cancelled.
   *
   * The debounce is 800ms and this unmounts the moment the board closes — Escape, the close
   * button, or simply clicking the other chat pane, since only the focused pane mounts the
   * overlay. Clearing the timer without writing meant the last thing you did to a board was the
   * one thing it never remembered, which reads as the board losing your work at random. */
  useEffect(() => {
    return () => {
      if (saving.current) clearTimeout(saving.current);
      const last = pending.current;
      pending.current = null;
      if (last) void saveWorkspaceFile(path, writeBoard(last), projectId).catch(() => {});
    };
  }, [path, projectId]);

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

  const loaded: BoardDesign[] = designs.map((one, at) => {
    const query = files[at];
    return {
      file: one.file,
      title: one.title,
      html: query?.data?.content ?? "",
      // "Opening…" forever is the wrong answer for a file that will never arrive. A design can
      // genuinely fail to read — two files sharing a basename make the workspace route's search
      // ambiguous — and a blank frame with a hopeful caption hides that completely.
      failed: query?.isError
        ? query.error instanceof Error
          ? query.error.message
          : "couldn't read it"
        : undefined,
    };
  });

  return (
    /* No header of its own.
     *
     * There was one — "Back · Designs · N on this board" — and every word of it was already on
     * the overlay bar directly above: the same count, and a close. Two bars saying one thing,
     * and the board itself, which is the whole point of the surface, started 96px further
     * down. The one part that was not a duplicate — that a double-click opens a frame — moved
     * into the toolbar, next to the tools it is about. `Back` belonged to the editor, not
     * here; from the board there is nothing behind but the overlay's own close. */
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1">
        <DesignBoard
          designs={loaded}
          board={board}
          onChange={change}
          onOpen={setOpen}
          projectId={projectId}
          onBack={onBack}
        />
      </div>
    </div>
  );
}

export { EMPTY as EMPTY_BOARD };
