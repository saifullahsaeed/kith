/**
 * The canvas in a reply: the designs a turn produced or showed, under the turn.
 *
 * "Canvas" is what the person and Kith call the board. The name used to belong to the ```html
 * fence, which ran as a live sealed page in the reply; that was retired and this took its place.
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
import { frameAttribute, readFrame } from "@/lib/design/frame";
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

/** The card's own width, in px. `w-66` in the class list below, named here because the
 *  thumbnail has to scale to it. */
const CARD_W = 264;

/** How tall a thumbnail is when the design is a wide one, and the most it may grow to.
 *
 *  A phone is more than twice as tall as it is wide, so its true aspect would make one card
 *  taller than the reply it sits in. Capped, which crops the bottom of a long screen — the
 *  same thing the board does, and the right half to keep: a thumbnail is for recognising a
 *  screen, and what identifies one is its top. */
const THUMB_H = 124;
const THUMB_H_MAX = 232;

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
      state.message.parts.flatMap((part): Made[] => {
        if (part.type !== "tool-call") return [];
        const { value, failed } = unwrap((part as { result?: unknown }).result);
        if (failed || !value || typeof value !== "object") return [];
        const row = value as Record<string, unknown>;
        // `show_canvas` (and `show_board`, its old name, in older transcripts): the designs he
        // chose to put in front of you, which may have been made in an earlier turn. This is
        // what makes the canvas appear in the reply that says "have a look", not only in the one
        // that happened to attach the files.
        if (part.toolName === "show_canvas" || part.toolName === "show_board") {
          const taskId = Number((part as { args?: { id?: unknown } }).args?.id);
          if (row.shown !== true || !Number.isFinite(taskId) || !Array.isArray(row.designs)) return [];
          const projectId = typeof row.project_id === "number" ? row.project_id : null;
          return row.designs.flatMap((one: unknown) => {
            const design = one as { file?: unknown; title?: unknown };
            if (typeof design.file !== "string" || !looksLikeADesign("file", design.file)) return [];
            return [{ file: design.file, title: String(design.title ?? design.file), taskId, projectId }];
          });
        }
        if (part.toolName !== "add_deliverable") return [];
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
  // Attached and then shown in the same turn is one design, not two.
  made = made.filter((one, at) => made.findIndex((other) => other.file === one.file) === at);
  if (!made.length) return null;

  return <Strip made={made} />;
}

/** The canvas, as it sits in a reply: the designs, and the way onto the full canvas.
 *
 *  This is the place the ```html canvas used to occupy — at the level of the answer, open, next
 *  to the prose about it. A turn that touched designs on more than one task gets one button per
 *  task, since a canvas is a task's. */
function Strip({ made }: { made: Made[] }) {
  const show = useBoardOverlay((state) => state.show);
  const tasks = made.filter((one, at) => made.findIndex((other) => other.taskId === one.taskId) === at);

  return (
    <div className="mt-3 flex flex-col gap-2 rounded-2xl bg-muted/20 p-3 ring-1 ring-border/50">
      <div className="flex items-center gap-2">
        <div className="flex items-center gap-1.5 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
          <Frame className="size-3.5" />
          Canvas · {made.length} design{made.length === 1 ? "" : "s"}
        </div>
        <div className="ms-auto flex gap-1.5">
          {tasks.map((one) => (
            <button
              key={one.taskId}
              type="button"
              onClick={() => show({ taskId: one.taskId, projectId: one.projectId, title: one.title })}
              className="rounded-lg px-2 py-1 text-[11px] font-medium ring-1 ring-border/60 transition-colors hover:bg-accent"
            >
              {tasks.length > 1 ? `Open canvas · task ${one.taskId}` : "Open canvas"}
            </button>
          ))}
        </div>
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

  const frame = readFrame(frameAttribute(file.data?.content ?? ""));
  const box = Math.min(
    THUMB_H_MAX,
    Math.max(THUMB_H, Math.round(CARD_W * (frame.height / frame.width))),
  );

  return (
    <button
      type="button"
      onClick={() => show({ taskId: made.taskId, projectId: made.projectId, title: made.title })}
      title={`${made.title} — open the canvas`}
      className="group w-66 overflow-hidden rounded-xl text-start ring-1 ring-border/60 transition-shadow hover:shadow-md"
    >
      <div className="relative overflow-hidden bg-muted/30" style={{ height: box }}>
        {file.data?.content ? (
          // Inert. This is a picture of the design; the click belongs to the button around it.
          //
          // Scaled from the design's *own* width. It used to be a constant 1200, so anything
          // not authored at that width was wrong in the one place these are actually looked
          // at: a 390-wide phone drew at 86px and sat crammed against the left edge of a
          // 264px card with the rest of it empty.
          <div
            className="pointer-events-none origin-top-left"
            style={{ transform: `scale(${CARD_W / frame.width})`, width: frame.width }}
          >
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
          Open canvas
        </span>
      </div>
      <div className="truncate px-2.5 py-1.5 text-[11px] font-medium">{made.title}</div>
    </button>
  );
}
