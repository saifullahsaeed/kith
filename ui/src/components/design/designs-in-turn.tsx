/**
 * The designs a turn produced, shown under the turn that produced them.
 *
 * ── Why this is not the tool result ─────────────────────────────────────────────────────────
 *
 * A design deliverable also renders as a card in `tool-result.tsx`, and that card was invisible.
 * Tool calls are grouped into a chain and the chain is a closed disclosure — a turn reads
 * "3 tasks · 12 commands · 1 skill · 5 files", one line, shut. So he made eight screens, the app
 * held a live thumbnail of every one of them, and what a person saw was a sentence containing
 * the word "attached".
 *
 * A design is not a tool call worth auditing. It is the output — the reason the turn happened —
 * and it belongs at the level of the answer, next to the prose about it, open. Tool rows stay
 * exactly as they are for everything that genuinely is a step.
 *
 * ── The envelope, which this file got wrong first time ──────────────────────────────────────
 *
 * `part.result` is not what the tool returned; it is `{ok, result}` around it. Reading the outer
 * object made every field `undefined`, the guard rejected every part, and the strip rendered
 * nothing at all — for a week, with no error anywhere. `unwrap` is in `lib/tool-envelope.ts`
 * because this was the second time it happened.
 *
 * ── Why it reads the parts rather than being handed them ────────────────────────────────────
 *
 * The message already holds every call it made, with arguments and results. Anything else — an
 * adapter field, a side channel from the stream — would be a second copy of a fact the transcript
 * already carries, and would be missing on every turn that happened before it was added. Read
 * this way, a conversation from last week shows its designs too.
 */
import { Frame } from "lucide-react";
import { useAuiState } from "@assistant-ui/react";
import { useQuery } from "@tanstack/react-query";

import { DesignView } from "@/components/design/design-view";
import { fetchWorkspaceFile } from "@/lib/backend/brain";
import { useBoardOverlay } from "@/lib/design/board-overlay";
import { looksLikeADesign } from "@/lib/design/detect";
import { unwrap } from "@/lib/tool-envelope";

interface Made {
  file: string;
  title: string;
  taskId: number;
  /** The deliverable's own project. Carried, not inferred — see migration v47. */
  projectId: number | null;
}

/** The scale a 1200px design is drawn at in a thumbnail. */
const THUMB = 0.22;

export function DesignsInTurn() {
  /* One subscription returning one string, for the reason `ActivityRun` gives a few files over:
   * the state selector compares by identity, so building an array here would be a new array
   * every render and therefore a render every render, on a thread that re-renders per streamed
   * chunk.
   *
   * The whole list is stringified once rather than each item joined by a separator. The first
   * version joined with a delimiter and split on it, which is the trick `ActivityRun` uses — but
   * that one joins tool *names*, and these are titles. "Option A — company beside the title"
   * contains the separator, so the split shattered the JSON and `JSON.parse` threw inside a
   * message body, taking the whole conversation down to its error boundary. */
  const packed = useAuiState((state) =>
    JSON.stringify(
      state.message.parts.flatMap((part) => {
        if (part.type !== "tool-call" || part.toolName !== "add_deliverable") return [];
        const { value, failed } = unwrap((part as { result?: unknown }).result);
        if (failed || !value || typeof value !== "object") return [];
        const row = value as Record<string, unknown>;
        const kind = row.kind;
        const content = row.content;
        const taskId = row.task_id;
        if (typeof kind !== "string" || typeof content !== "string") return [];
        if (typeof taskId !== "number" || !looksLikeADesign(kind, content)) return [];
        // A deliverable knows its own project now, so a thumbnail can resolve a design's assets
        // and its system without first fetching the task to find out where it lives.
        const projectId = typeof row.project_id === "number" ? row.project_id : null;
        return [{ file: content, title: String(row.title ?? content), taskId, projectId }];
      }),
    ),
  );

  // Guarded, because a parse that throws here throws inside the message body — the failure mode
  // the first version actually had.
  let made: Made[] = [];
  try {
    made = JSON.parse(packed) as Made[];
  } catch {
    made = [];
  }
  if (!made.length) return null;

  return (
    <div className="mt-3 flex flex-col gap-2">
      <div className="flex items-center gap-1.5 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
        <Frame className="size-3.5" />
        {made.length} design{made.length === 1 ? "" : "s"}
      </div>
      <div className="flex flex-wrap gap-2">
        {made.map((one) => (
          <Thumb key={one.file} made={one} />
        ))}
      </div>
    </div>
  );
}

function Thumb({ made }: { made: Made }) {
  const show = useBoardOverlay((state) => state.show);
  const file = useQuery({
    queryKey: ["design", made.file, made.projectId],
    queryFn: () => fetchWorkspaceFile(made.file, made.projectId),
  });

  return (
    <button
      type="button"
      onClick={() => show({ taskId: made.taskId, projectId: made.projectId, title: made.title })}
      title={`${made.title} — open the board`}
      className="group w-66 overflow-hidden rounded-xl text-start ring-1 ring-border/60 transition-shadow hover:shadow-md"
    >
      <div className="relative h-31 overflow-hidden bg-muted/30">
        {file.data?.content ? (
          // Inert. This is a picture of the design; the click belongs to the button around it.
          <div className="pointer-events-none origin-top-left" style={{ transform: `scale(${THUMB})`, width: 1200 }}>
            <DesignView html={file.data.content} projectId={made.projectId} />
          </div>
        ) : (
          <div className="flex h-full items-center justify-center px-3 text-center text-[11px] text-muted-foreground">
            {file.isError
              ? (file.error instanceof Error ? file.error.message : "couldn't read it")
              : "…"}
          </div>
        )}
        <span className="absolute inset-0 flex items-center justify-center bg-background/70 text-xs font-medium opacity-0 transition-opacity group-hover:opacity-100">
          Open board
        </span>
      </div>
      <div className="truncate px-2.5 py-1.5 text-[11px] font-medium">{made.title}</div>
    </button>
  );
}
