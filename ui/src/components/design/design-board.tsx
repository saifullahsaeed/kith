/**
 * The board — every design on a task, side by side, at whatever zoom the comparison needs.
 *
 * This is the surface the editor was missing. An editor answers "is this screen right"; the
 * question design work actually asks is "is this one better than that one", and that question is
 * a glance across four variants rather than a memory test between two page loads. Everything
 * here exists to make that glance possible: one plane, many frames, and writing on it.
 *
 * ── The frames are live, not pictures ───────────────────────────────────────────────────────
 *
 * Each frame is a real `DesignView` scaled down, not a screenshot. It costs more than an image
 * and buys the thing a board is for: what you are comparing is what will be built, it follows
 * the theme, and clicking into one is continuous with looking at it rather than a different
 * object opening somewhere else.
 *
 * ── Zoom is at the pointer, and that is not a detail ────────────────────────────────────────
 *
 * Zooming to the centre of the viewport makes a board unusable at the exact moment it becomes
 * useful — twelve frames in, you point at the one you care about, zoom, and it leaves the screen.
 * The arithmetic that keeps the point under the cursor fixed is four lines and is the difference
 * between a canvas and a scroll area.
 */
import { Hand, Maximize2, MousePointer2, Plus, StickyNote } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type WheelEvent } from "react";

import { DesignView } from "@/components/design/design-view";
import {
  type Board,
  type BoardFrame,
  type BoardNote,
  extent,
  FRAME_H,
  layout,
  ZOOM_RANGE,
} from "@/lib/design/board";
import { cn } from "@/lib/utils";

/** The width a design is authored against, and therefore what a frame scales down from. */
const DESIGN_W = 1200;

export interface BoardDesign {
  /** The deliverable's path — the board's key for this frame. */
  file: string;
  title: string;
  /** The file's contents, or "" while it is still being read. */
  html: string;
  /** Why it will never arrive, when the read failed. */
  failed?: string;
}

export interface DesignBoardProps {
  designs: BoardDesign[];
  board: Board;
  onChange: (board: Board) => void;
  /** Open one design in the editor. */
  onOpen: (file: string) => void;
}

type Tool = "select" | "hand" | "note";

export function DesignBoard({ designs, board, onChange, onOpen }: DesignBoardProps) {
  const surface = useRef<HTMLDivElement | null>(null);
  const [tool, setTool] = useState<Tool>("select");
  const [view, setView] = useState(board.view ?? { x: 48, y: 48, zoom: 0.45 });
  const [picked, setPicked] = useState<string | null>(null);
  const [editingNote, setEditingNote] = useState<string | null>(null);

  const frames = layout(designs.map((one) => one.file), board);

  /* The viewport is held locally and written back on settle.
   *
   * Every wheel tick would otherwise be a file write — a few hundred of them to cross a board —
   * and the thing being saved is where somebody was looking, which nobody needs to the frame.
   *
   * Two bugs lived in the first version of this, both from the same cause: the effect closed over
   * `board` from the render in which `view` last changed, and deliberately did not depend on it.
   *
   * * It wrote back a **stale** board. Drag a frame within 600ms of the last pan and the timer
   *   fired carrying the pre-drag arrangement, and the parent took that as authoritative — so
   *   the drag silently reverted.
   * * It wrote at all on **mere open**. A board nobody has arranged has no stored view, so the
   *   comparison was false on the first pass and opening one created a file 600ms later with no
   *   user action behind it.
   *
   * So: the latest board is read from a ref rather than captured, and nothing is written until
   * somebody has actually moved something. */
  const latest = useRef(board);
  latest.current = board;
  const touched = useRef(false);

  useEffect(() => {
    if (!touched.current) return;
    const timer = setTimeout(() => {
      const now = latest.current;
      if (now.view?.x === view.x && now.view?.y === view.y && now.view?.zoom === view.zoom) return;
      onChange({ ...now, view });
    }, 600);
    return () => clearTimeout(timer);
    // `onChange` is stable from the parent; `latest` is a ref by design — see above for why
    // depending on `board` here is what made it never settle.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view]);

  const toBoard = useCallback(
    (clientX: number, clientY: number) => {
      const box = surface.current?.getBoundingClientRect();
      if (!box) return { x: 0, y: 0 };
      return { x: (clientX - box.left - view.x) / view.zoom, y: (clientY - box.top - view.y) / view.zoom };
    },
    [view],
  );

  /* Bound by hand, not through React's `onWheel`.
   *
   * React registers `wheel` passively on the root, so a handler there cannot `preventDefault` —
   * and without that, ⌘+wheel reaches Chromium's own page zoom (the whole app scales while the
   * board scales) and a plain two-finger scroll bubbles to the conversation behind the overlay.
   * `mermaid-diagram.tsx` already documents and solves exactly this. */
  useEffect(() => {
    const node = surface.current;
    if (!node) return;
    const handler = (event: globalThis.WheelEvent) => {
      event.preventDefault();
      onWheel(event as unknown as WheelEvent);
    };
    node.addEventListener("wheel", handler, { passive: false });
    return () => node.removeEventListener("wheel", handler);
  });

  const onWheel = (event: WheelEvent) => {
    // Ctrl/⌘ + wheel is zoom everywhere, and a trackpad pinch arrives as exactly that.
    if (event.ctrlKey || event.metaKey) {
      const box = surface.current?.getBoundingClientRect();
      if (!box) return;
      const px = event.clientX - box.left;
      const py = event.clientY - box.top;
      setView((current) => {
        const next = Math.min(
          ZOOM_RANGE[1],
          Math.max(ZOOM_RANGE[0], current.zoom * Math.exp(-event.deltaY / 240)),
        );
        // Keep the board point under the cursor exactly where it is.
        return {
          zoom: next,
          x: px - (px - current.x) * (next / current.zoom),
          y: py - (py - current.y) * (next / current.zoom),
        };
      });
      return;
    }
    setView((current) => ({ ...current, x: current.x - event.deltaX, y: current.y - event.deltaY }));
  };

  /** Dragging: the board under the hand tool, a frame or a note under select. */
  const drag = useRef<{ kind: "board" | "frame" | "note"; id: string; ox: number; oy: number } | null>(null);

  const startBoardDrag = (event: ReactPointerEvent) => {
    // Middle button pans whatever the tool is, which is the habit every canvas shares.
    if (tool !== "hand" && event.button !== 1) {
      setPicked(null);
      setEditingNote(null);
      return;
    }
    (event.target as HTMLElement).setPointerCapture?.(event.pointerId);
    drag.current = { kind: "board", id: "", ox: event.clientX - view.x, oy: event.clientY - view.y };
  };

  const startItemDrag = (
    event: ReactPointerEvent,
    kind: "frame" | "note",
    id: string,
    at: { x: number; y: number },
  ) => {
    if (tool === "hand") return;
    event.stopPropagation();
    (event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);
    const point = toBoard(event.clientX, event.clientY);
    drag.current = { kind, id, ox: point.x - at.x, oy: point.y - at.y };
    setPicked(id);
    touched.current = true;

    /* Commit every derived position before the first move.
     *
     * `layout()` splits frames into ones with a stored position and ones auto-gridded below
     * them. Writing only the dragged frame therefore *promoted* it into the stored set, and on
     * the very next render every other frame was re-gridded underneath it — so picking up one
     * card threw all the others across the board, and they then tracked it for the rest of the
     * drag. Once nothing is loose, nothing can be re-gridded. */
    if (kind === "frame") {
      const loose = Object.keys(frames).some((file) => !board.frames[file]);
      if (loose) onChange({ ...board, frames: { ...frames } });
    }
  };

  const onPointerMove = (event: ReactPointerEvent) => {
    const held = drag.current;
    if (!held) return;
    if (held.kind === "board") {
      setView((current) => ({ ...current, x: event.clientX - held.ox, y: event.clientY - held.oy }));
      return;
    }
    const point = toBoard(event.clientX, event.clientY);
    const x = Math.round(point.x - held.ox);
    const y = Math.round(point.y - held.oy);

    if (held.kind === "frame") {
      const current = frames[held.id];
      if (!current) return;
      onChange({ ...board, frames: { ...board.frames, [held.id]: { ...current, x, y } } });
    } else {
      onChange({
        ...board,
        notes: board.notes.map((note) => (note.id === held.id ? { ...note, x, y } : note)),
      });
    }
  };

  const endDrag = () => {
    drag.current = null;
  };

  const addNote = (event: ReactPointerEvent) => {
    touched.current = true;
    const point = toBoard(event.clientX, event.clientY);
    const id = `n${Date.now().toString(36)}`;
    onChange({
      ...board,
      notes: [...board.notes, { id, x: Math.round(point.x), y: Math.round(point.y), w: 240, text: "" }],
    });
    setTool("select");
    setEditingNote(id);
  };

  const fit = () => {
    const box = surface.current?.getBoundingClientRect();
    if (!box) return;
    const bounds = extent(frames, board.notes);
    const zoom = Math.min(
      ZOOM_RANGE[1],
      Math.max(ZOOM_RANGE[0], Math.min((box.width - 96) / bounds.w, (box.height - 96) / bounds.h)),
    );
    setView({
      zoom,
      x: (box.width - bounds.w * zoom) / 2 - bounds.x * zoom,
      y: (box.height - bounds.h * zoom) / 2 - bounds.y * zoom,
    });
  };

  /* Space-to-pan, the one shortcut every canvas has and nobody is told about.
   *
   * Two things this has to get right, and the first version got neither. It must not fire while
   * somebody is typing — these are `v`, `n` and space, which is most of writing a note or a
   * message — so anything aimed at an editable element is ignored. And releasing space must
   * restore the tool that was active *before*, not hardcode "select": someone who deliberately
   * chose the hand tool and then typed a space found themselves back on the pointer. */
  const beforeSpace = useRef<Tool | null>(null);
  useEffect(() => {
    const typing = (target: EventTarget | null) => {
      const el = target as HTMLElement | null;
      if (!el) return false;
      const tag = el.tagName;
      return el.isContentEditable || tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
    };

    const down = (event: KeyboardEvent) => {
      if (editingNote || typing(event.target)) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.code === "Space") {
        if (beforeSpace.current === null) {
          setTool((current) => {
            beforeSpace.current = current;
            return "hand";
          });
        }
        return;
      }
      if (event.key === "v") setTool("select");
      if (event.key === "n") setTool("note");
    };

    const up = (event: KeyboardEvent) => {
      if (event.code !== "Space") return;
      const restore = beforeSpace.current;
      beforeSpace.current = null;
      if (restore !== null && !editingNote) setTool(restore);
    };

    addEventListener("keydown", down);
    addEventListener("keyup", up);
    return () => {
      removeEventListener("keydown", down);
      removeEventListener("keyup", up);
    };
  }, [editingNote]);

  const byFile = new Map(designs.map((one) => [one.file, one]));

  return (
    <div className="relative flex h-full flex-col">
      <Toolbar tool={tool} setTool={setTool} zoom={view.zoom} onFit={fit} />

      <div
        ref={surface}
        onPointerDown={tool === "note" ? addNote : startBoardDrag}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        className={cn(
          "relative min-h-0 flex-1 overflow-hidden bg-muted/40",
          // The dotted ground, which is how a canvas reads as a plane rather than a page.
          "[background-image:radial-gradient(var(--color-border)_1px,transparent_1px)]",
          tool === "hand" && "cursor-grab active:cursor-grabbing",
          tool === "note" && "cursor-crosshair",
        )}
        style={{
          backgroundSize: `${24 * view.zoom}px ${24 * view.zoom}px`,
          backgroundPosition: `${view.x}px ${view.y}px`,
        }}
      >
        <div
          className="absolute top-0 left-0 origin-top-left"
          style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.zoom})` }}
        >
          {Object.entries(frames).map(([file, frame]) => (
            <Frame
              key={file}
              frame={frame}
              design={byFile.get(file)}
              picked={picked === file}
              zoom={view.zoom}
              onPointerDown={(event) => startItemDrag(event, "frame", file, frame)}
              onOpen={() => onOpen(file)}
            />
          ))}

          {board.notes.map((note) => (
            <Note
              key={note.id}
              note={note}
              editing={editingNote === note.id}
              onPointerDown={(event) => startItemDrag(event, "note", note.id, note)}
              onEdit={() => setEditingNote(note.id)}
              onDone={() => setEditingNote(null)}
              onText={(text) =>
                onChange({
                  ...board,
                  notes: board.notes.map((one) => (one.id === note.id ? { ...one, text } : one)),
                })
              }
              onRemove={() =>
                onChange({ ...board, notes: board.notes.filter((one) => one.id !== note.id) })
              }
            />
          ))}
        </div>

        {!designs.length ? (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center px-8 text-center text-sm text-muted-foreground">
            Nothing on this board yet. Attach an HTML file to the task and it appears here.
          </div>
        ) : null}
      </div>
    </div>
  );
}

function Toolbar({
  tool,
  setTool,
  zoom,
  onFit,
}: {
  tool: Tool;
  setTool: (tool: Tool) => void;
  zoom: number;
  onFit: () => void;
}) {
  return (
    <div className="flex shrink-0 items-center gap-1 border-b border-border/70 px-3 py-2">
      <ToolButton label="Select — V" active={tool === "select"} onClick={() => setTool("select")}>
        <MousePointer2 className="size-4" />
      </ToolButton>
      <ToolButton label="Pan — H, or hold space" active={tool === "hand"} onClick={() => setTool("hand")}>
        <Hand className="size-4" />
      </ToolButton>
      <ToolButton label="Note — N" active={tool === "note"} onClick={() => setTool("note")}>
        <StickyNote className="size-4" />
      </ToolButton>

      <div className="mx-1 h-5 w-px bg-border/70" />

      <ToolButton label="Fit to screen" onClick={onFit}>
        <Maximize2 className="size-4" />
      </ToolButton>
      <span className="ms-1 w-12 text-center text-xs tabular-nums text-muted-foreground">
        {Math.round(zoom * 100)}%
      </span>
    </div>
  );
}

function ToolButton({
  label,
  active,
  onClick,
  children,
}: {
  label: string;
  active?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      onClick={onClick}
      className={cn(
        "flex size-8 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground",
        active && "bg-kith-soft text-kith",
      )}
    >
      {children}
    </button>
  );
}

function Frame({
  frame,
  design,
  picked,
  zoom,
  onPointerDown,
  onOpen,
}: {
  frame: BoardFrame;
  design?: BoardDesign;
  picked: boolean;
  zoom: number;
  onPointerDown: (event: ReactPointerEvent) => void;
  onOpen: () => void;
}) {
  const scale = frame.w / DESIGN_W;

  /* The counter-scale that keeps a name readable at 26%.
   *
   * It has to be exactly `1 / zoom`: on-screen size is 11px × zoom × scale, so anything else
   * leaves a residue. The first version used `1 / (2.2 × zoom)`, which pinned every label to
   * 5 device pixels at *every* zoom below 45% and applied no counter-scale at all above it —
   * the one band where it did nothing being the one where it was least needed.
   *
   * Clamped, because at the 8% floor a full counter-scale makes the label taller than its frame.
   * The width shrinks with it so the hit box stays over its own frame rather than reaching
   * across its neighbour. */
  const label = Math.min(4, Math.max(1, 1 / Math.max(zoom, 0.0001)));

  return (
    <div className="group absolute" style={{ left: frame.x, top: frame.y, width: frame.w }}>
      <div
        className="flex origin-bottom-left items-center gap-2 pb-1"
        style={{ transform: `scale(${label})`, width: frame.w / label }}
        onPointerDown={onPointerDown}
      >
        <span className="truncate text-[11px] font-medium text-muted-foreground">
          {design?.title ?? frame.w}
        </span>
        <button
          type="button"
          onClick={onOpen}
          className="rounded-md px-1.5 py-0.5 text-[10px] text-muted-foreground opacity-0 transition-opacity hover:bg-accent hover:text-foreground group-hover:opacity-100"
        >
          Edit
        </button>
      </div>

      <div
        onPointerDown={onPointerDown}
        onDoubleClick={onOpen}
        className={cn(
          "relative overflow-hidden rounded-lg border bg-background shadow-sm",
          picked ? "border-kith ring-2 ring-kith/40" : "border-border/70",
        )}
        style={{ width: frame.w, height: FRAME_H }}
      >
        {/* Nothing inside a frame is interactive on the board — a click there is a click on the
            frame. Otherwise dragging a card would mean dragging whatever button is under the
            pointer, and selecting a variant would select a node inside it. */}
        <div className="pointer-events-none origin-top-left" style={{ transform: `scale(${scale})`, width: DESIGN_W }}>
          {design?.html ? (
            <DesignView html={design.html} />
          ) : (
            <div className="flex h-40 items-center justify-center px-6 text-center text-sm text-muted-foreground">
              {design?.failed ?? "Opening…"}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Note({
  note,
  editing,
  onPointerDown,
  onEdit,
  onDone,
  onText,
  onRemove,
}: {
  note: BoardNote;
  editing: boolean;
  onPointerDown: (event: ReactPointerEvent) => void;
  onEdit: () => void;
  onDone: () => void;
  onText: (text: string) => void;
  onRemove: () => void;
}) {
  return (
    <div
      className="absolute rounded-md bg-amber-200/90 p-2 text-[11px] leading-snug text-amber-950 shadow-sm dark:bg-amber-300/80"
      style={{ left: note.x, top: note.y, width: note.w }}
      onPointerDown={onPointerDown}
      onDoubleClick={onEdit}
    >
      {editing ? (
        <textarea
          autoFocus
          value={note.text}
          onChange={(event) => onText(event.target.value)}
          onBlur={() => {
            // An empty note is a misclick, not a note.
            if (!note.text.trim()) onRemove();
            onDone();
          }}
          onPointerDown={(event) => event.stopPropagation()}
          className="h-24 w-full resize-none bg-transparent outline-none"
          placeholder="what this one is"
        />
      ) : note.text ? (
        <div className="whitespace-pre-wrap">{note.text}</div>
      ) : (
        <div className="flex items-center gap-1 opacity-60">
          <Plus className="size-3" />
          double-click to write
        </div>
      )}
    </div>
  );
}
