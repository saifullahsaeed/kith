import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { Code2, Maximize2, RotateCw, X } from "lucide-react";

import { useAuiState } from "@assistant-ui/react";
import type { SyntaxHighlighterProps } from "@assistant-ui/react-markdown";

import { OverlayButton } from "@/components/assistant-ui/overlay-button";
import { readCanvasMessage, themeMessage } from "@/lib/canvas-bridge";
import { useCanvasState } from "@/lib/canvas-state";
import { FRAME_SANDBOX, isComplete, looksRenderable, sealedDocument } from "@/lib/canvas";
import { paletteFor } from "@/lib/kith-palette";
import { useDarkMode } from "@/lib/theme";
import { cn } from "@/lib/utils";

/**
 * An ```html block, running.
 *
 * The second half of the argument made in `lib/canvas.ts`: the fence is what he already writes,
 * so this component's whole job is to stop showing it as source. What lives here is only the
 * part that is about React and the screen — when to mount the frame, how tall it is, and how to
 * get the source back when the guess was wrong.
 *
 * Three things it has to get right, each of which is a real failure it would otherwise have:
 *
 * **It must not mount mid-sentence.** A half-written document is not an error the way a
 * half-written diagram is — the browser renders it, silently discarding the unfinished script,
 * and you get a page that looks finished and does nothing. So the frame waits for the blocks to
 * close *and* for the tokens to stop, and shows a placeholder until then. Rebuilding on every
 * token would also restart the animation on every token.
 *
 * **A wrong guess must be one click from being right.** `looksRenderable` is a heuristic about
 * someone else's output. When it decides a listing is a drawing, the source has to be there —
 * not in a menu, not by asking him again.
 *
 * **It cannot measure itself.** A null-origin frame is not permitted to tell the page how tall
 * its content is, and it should not be — the way to learn that is a message channel, which is
 * exactly the thing this half is deliberately not opening yet. So height is ours to decide: a
 * sensible default you can drag, and full screen for anything that wanted more.
 */

/** Tall enough for a diagram or a small scene, short enough that two in a row are still a
 *  conversation. Drag the bottom edge for anything else. */
const DEFAULT_HEIGHT = 380;

/** How tall the box in the conversation is allowed to get, however it got that way — dragged or
 *  measured. Not the same range as `HEIGHT_RANGE` in `canvas-bridge.ts`, and the two are
 *  deliberately different things: that one is how tall a page may *claim* to be, a bound on
 *  input from a frame we do not trust, and it is wider because the claim is also what the
 *  full-screen view honours. This one is how tall this rectangle may *be*.
 *
 *  They were both called `HEIGHT_RANGE`, and a self-measured 2000px page took the wider one:
 *  the canvas came up tall, and the first nudge of the drag handle — which clamped to this one
 *  — collapsed it by eight hundred pixels in a single frame. Anything that sets the height now
 *  goes through `boxed` so a drag can only ever continue from a size the drag itself could
 *  have reached. */
const BOX_HEIGHT = [160, 1200] as const;

const boxed = (px: number) => Math.min(BOX_HEIGHT[1], Math.max(BOX_HEIGHT[0], px));

/** How long the code has to stop changing before the frame is built. Matches the diagram's
 *  settle window, and for the same reason: long enough never to fire between two tokens of the
 *  same block, short enough that a finished one appears promptly. */
const SETTLE_MS = 220;

/** And how long to keep waiting for blocks that never close. Without this, a document he never
 *  finished — a truncated reply, a `<script` he opened and abandoned — leaves a placeholder on
 *  screen forever, which is the one outcome worse than showing the source. */
const GIVE_UP_MS = 2600;

/** The assistant-ui contract, mirroring `MermaidBlock`. What it supplies is the fallback: the
 *  ordinary code block, built from the `Pre`/`Code` the library hands over, so anything this
 *  component declines to draw renders exactly as it did before this existed. */
export function HtmlCanvasBlock({ code, components: { Pre, Code } }: SyntaxHighlighterProps) {
  /* Whether this reply is still being written, asked of the thread rather than guessed from the
     text. The settle timer alone was not enough and the way it failed is worth keeping: a long
     page passes through *many* momentarily-valid states on its way in — the instant `</style>`
     closes, every block this file counts is balanced — so a pause between tokens looked like a
     finished document. Each one built, POSTed and mounted a frame, and the next token tore it
     down again. That is the whole of the "the UI is blinking" report: not a render loop, a page
     being loaded and discarded ten times while he wrote it. */
  const streaming = useAuiState((state) => state.message.status?.type === "running");
  return (
    <HtmlCanvas
      streaming={streaming}
      code={code}
      fallback={
        <Pre>
          <Code>{code}</Code>
        </Pre>
      }
    />
  );
}

export function HtmlCanvas({
  code,
  fallback,
  streaming = false,
}: {
  code: string;
  fallback: ReactNode;
  /** False for a file in the viewer, which is never half-written. */
  streaming?: boolean;
}) {
  const dark = useDarkMode();
  /** The URL the last document worth mounting is being served from. Held across re-renders so a
   *  theme change or a replay does not have to go back through the settle window.
   *
   *  A URL rather than the document itself, and that is not a detail — a framed document on a
   *  local scheme (`srcdoc`, `blob:`, `data:` — measured, all three) inherits the embedder's CSP,
   *  and this app is served with `script-src 'self'`. Mounted that way a canvas renders its
   *  markup and runs none of its script. `POST /api/canvas` hands the document back as an
   *  ordinary http URL, which inherits nothing and carries the seal in its own headers. See
   *  `server/kith/api/routes/canvas.py`. */
  const [doc, setDoc] = useState("");
  const [stalled, setStalled] = useState(false);
  const [showSource, setShowSource] = useState(false);
  const [zoomed, setZoomed] = useState(false);
  const [height, setHeight] = useState(DEFAULT_HEIGHT);
  /** Set the moment you drag the bottom edge, and never unset. A canvas that re-measured itself
   *  after you had chosen a size would undo the choice, which is worse than a canvas that never
   *  measured at all. */
  const [sized, setSized] = useState(false);
  /** Bumped by the replay button, and used as the frame's key — remounting is the only way to
   *  restart a page whose animation we are not allowed to talk to. */
  const [generation, setGeneration] = useState(0);

  const report = useCanvasState((state) => state.report);
  const forget = useCanvasState((state) => state.forget);

  /* What the canvas tells us about itself. Height is applied here; state is put where the next
     message will find it — see `canvas-state.ts` for why moving a slider does not start a turn. */
  const heard = useCallback(
    (message: ReturnType<typeof readCanvasMessage>) => {
      if (!message) return;
      if (message.type === "height") {
        if (!sized) setHeight(boxed(message.px));
        return;
      }
      report(doc, { title: message.title, values: message.values });
    },
    [doc, report, sized],
  );

  /* The same wire, from the full-screen frame — minus the height.
     A page measures itself against the window it is in, and full screen is a different window;
     letting that reading back would resize the inline box to fit a viewport it is not in. What
     the controls read is the same question in both frames. How tall the page is, is not. */
  const heardZoomed = useCallback(
    (message: ReturnType<typeof readCanvasMessage>) => {
      if (!message || message.type === "height") return;
      report(doc, { title: message.title, values: message.values });
    },
    [doc, report],
  );

  /* A canvas nobody can see is not context. Scrolled away, replaced by a later reply, or the
     whole conversation closed — either way what its controls read is no longer something the
     person is looking at, and carrying it into the next turn would be describing a screen that
     is not on screen. */
  useEffect(() => () => forget(doc), [doc, forget]);

  // Both synchronous, so a canvas shows a placeholder from its first token rather than a flash
  // of source that turns into a drawing 220ms later. They are regexes; they can afford to run on
  // every render.
  const renderable = useMemo(() => looksRenderable(code), [code]);
  const complete = useMemo(() => isComplete(code), [code]);

  /* The theme at the moment of building, read through a ref so that changing it later does not
     land in the effect's dependencies. It used to, and the cost was the whole point of the port:
     switching to dark rebuilt the document, re-POSTed it and reloaded the frame, so every
     animation started over. Now the document is built once and repainted in place. */
  const theme = useRef(dark);
  theme.current = dark;

  useEffect(() => {
    if (!renderable || streaming) return;
    let cancelled = false;
    const build = setTimeout(() => {
      if (!isComplete(code)) return;
      void host(sealedDocument(code, paletteFor(theme.current))).then((url) => {
        if (cancelled || !url) return;
        setDoc(url);
        setStalled(false);
      });
    }, SETTLE_MS);
    // Restarted by every token, so this only fires once he has genuinely stopped writing.
    const abandon = setTimeout(() => setStalled(true), GIVE_UP_MS);
    return () => {
      cancelled = true;
      clearTimeout(build);
      clearTimeout(abandon);
    };
  }, [code, renderable, streaming]);

  // Markup he is showing you rather than drawing with — but only once he has finished writing
  // it. Half of a document does not look like a drawing yet: `<style>b{colo` has behaviour and
  // nothing to show, and calling that a listing means every canvas that opens with a stylesheet
  // flashes its own source before appearing. An unclosed block is not a verdict, it is a wait.
  if (!renderable && complete) return <>{fallback}</>;
  // A drawing he never finished — a truncated reply, a `<script` opened and abandoned. The
  // source is what is left that is useful.
  if (stalled && !doc) return <>{fallback}</>;

  return (
    <>
      <figure
        data-slot="kith_canvas"
        className="group border-border/60 bg-card/40 relative my-3 overflow-hidden rounded-xl border"
      >
        {showSource ? (
          <div className="max-h-[520px] overflow-auto">{fallback}</div>
        ) : doc ? (
          /* Keyed here rather than on the iframe inside. The key was on the element, so replay
             remounted the frame and left `Frame`'s own state standing — including the flag that
             says the frame has loaded, which then said "yes" about a document that had just been
             thrown away. Keying the component means a replayed canvas starts from the same place
             a new one does. */
          <Frame key={generation} doc={doc} height={height} dark={dark} onMessage={heard} />
        ) : (
          <Building bytes={code.length} streaming={streaming} />
        )}

        <div className="absolute end-2 top-2 flex gap-1 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100">
          <OverlayButton
            label={showSource ? "Show the canvas" : "Show the source"}
            onClick={() => setShowSource((on) => !on)}
          >
            <Code2 className={cn("size-3.5", showSource && "text-kith")} />
          </OverlayButton>
          {!showSource && doc ? (
            <>
              <OverlayButton
                label="Play it again from the start"
                onClick={() => setGeneration((n) => n + 1)}
              >
                <RotateCw className="size-3.5" />
              </OverlayButton>
              <OverlayButton label="Open the canvas full screen" onClick={() => setZoomed(true)}>
                <Maximize2 className="size-3.5" />
              </OverlayButton>
            </>
          ) : null}
        </div>

        {!showSource && doc ? (
          <ResizeHandle
            height={height}
            onChange={(next) => {
              setSized(true);
              setHeight(next);
            }}
          />
        ) : null}
      </figure>
      {zoomed ? (
        <Lightbox doc={doc} dark={dark} onMessage={heardZoomed} onClose={() => setZoomed(false)} />
      ) : null}
    </>
  );
}

/**
 * The sealed frame itself.
 *
 * Every capability it does not have is decided in `lib/canvas.ts` and applied here in two
 * attributes. Kept as its own component so that boundary is one small readable thing rather than
 * three lines buried in the middle of a layout.
 *
 * Replay is a remount — reassigning the same `src` is a no-op — so the caller keys this
 * component on the generation. Everything a frame knows about itself, including whether it has
 * loaded, is state in here and has to go with it.
 *
 * Used for the inline box and for the full-screen window both. The lightbox opening a bare
 * `<iframe>` of its own was the bug that made this a shared component: the bigger, more usable
 * surface was the one with no wire out and no theme in, so what you set full screen was thrown
 * away and the palette froze at whatever it was built with.
 */
function Frame({
  doc,
  height,
  dark,
  onMessage,
  className,
}: {
  doc: string;
  /** Absent for the full-screen frame, which is the size of the window. */
  height?: number;
  dark: boolean;
  onMessage: (message: ReturnType<typeof readCanvasMessage>) => void;
  className?: string;
}) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [alive, setAlive] = useState(false);

  /* The wire in. Identity is checked before anything else and by window rather than by origin:
     every sandboxed frame on the page reports its origin as the string "null", so origin
     distinguishes one canvas from another not at all — and this handler is on `window`, which
     hears from all of them. `event.source` is the one fact about a message that the sender cannot
     forge. See `canvas-bridge.ts` for what happens to the payload after that. */
  useEffect(() => {
    const listen = (event: MessageEvent) => {
      if (!frame.current || event.source !== frame.current.contentWindow) return;
      const message = readCanvasMessage(event.data);
      if (message) onMessage(message);
    };
    window.addEventListener("message", listen);
    return () => window.removeEventListener("message", listen);
  }, [onMessage]);

  /* The theme, pushed in. The palette inside the frame is custom properties, so this is a
     repaint — the page keeps running, and a canvas you were watching does not start over because
     you reached for the light switch. Sent on every change and once on load, since a frame that
     mounts mid-switch would otherwise keep the palette it was built with. */
  useEffect(() => {
    if (!alive) return;
    frame.current?.contentWindow?.postMessage(themeMessage(paletteFor(dark), dark), "*");
  }, [dark, alive]);

  return (
    <iframe
      ref={frame}
      title="Canvas"
      sandbox={FRAME_SANDBOX}
      src={doc}
      onLoad={() => setAlive(true)}
      className={cn("block w-full border-0 bg-transparent", className)}
      style={height === undefined ? undefined : { height }}
    />
  );
}

/**
 * Hand the document to the server and get back the address to frame it from.
 *
 * Null on any failure, and the caller treats that as "not ready yet" rather than as an error —
 * the give-up timer then falls back to the source, which is the same outcome as a document that
 * never finished. A canvas that cannot be served is not worth a red box in the middle of a reply.
 */
async function host(document: string): Promise<string | null> {
  try {
    const response = await fetch("/api/canvas", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ html: document }),
    });
    if (!response.ok) return null;
    const body = (await response.json()) as { url?: string };
    return body.url ?? null;
  } catch {
    return null;
  }
}

/**
 * Shown while the document is still arriving.
 *
 * Deliberately not the source — a canvas that flashed its own code before drawing would undo the
 * thing this component is for — but deliberately not a blank box either. The first version was
 * one word in an empty rectangle the height of nothing, held for however long a page takes to
 * write, which reads as broken rather than busy. So it says what is happening and shows it
 * growing: the byte count climbing is the only honest progress signal available, since the one
 * thing nobody knows is how long the page will turn out to be.
 *
 * A single line rather than a reserved rectangle. Guessing the finished height and holding that
 * much empty space would be wrong twice — wrong while it waits, and wrong again when the canvas
 * arrives and reports its real height.
 */
function Building({ bytes, streaming }: { bytes: number; streaming: boolean }) {
  return (
    <div className="animate-in fade-in flex flex-col gap-3 p-4 duration-300">
      <div className="text-muted-foreground/70 flex items-center gap-2.5 font-mono text-[11px]">
        <span className="bg-kith/70 size-1.5 shrink-0 animate-pulse rounded-full" />
        <span>{streaming ? "writing a page" : "building the page"}</span>
        <span className="text-muted-foreground/35 tabular-nums">
          {bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB`}
        </span>
      </div>
      {/* The shape of a page, not a spinner. Three bars and a block is what almost everything he
          draws looks like from far enough away — a title, a couple of lines, then the thing — so
          the space reads as "a canvas is arriving here" rather than as an empty box someone
          forgot to fill. Grows with the page: one bar at first, the block once there is enough
          written that it will certainly need one. */}
      <div className="flex flex-col gap-2" aria-hidden>
        <Bar className="w-2/5" />
        {bytes > 400 ? <Bar className="w-4/5" /> : null}
        {bytes > 900 ? <Bar className="w-3/5" /> : null}
        {bytes > 1600 ? (
          <div className="bg-muted/45 mt-1 h-24 animate-pulse rounded-lg [animation-duration:2.4s]" />
        ) : null}
      </div>
    </div>
  );
}

function Bar({ className }: { className: string }) {
  return <div className={cn("bg-muted/60 h-2.5 animate-pulse rounded [animation-duration:2s]", className)} />;
}

/**
 * The bottom edge, draggable.
 *
 * The honest answer to a frame that is not allowed to report its own height. A fixed size is
 * wrong for half of what he draws — a wide diagram wants less, a scene wants more — and the
 * alternative that measures the content needs a message channel out of the frame, which is a
 * bigger decision than "how tall is this box" should be allowed to force.
 *
 * Pointer capture rather than window listeners, so a drag that leaves the element still tracks,
 * and a drag that ends outside the window still ends.
 */
function ResizeHandle({ height, onChange }: { height: number; onChange: (h: number) => void }) {
  const from = useRef<{ y: number; height: number } | null>(null);

  const move = useCallback(
    (event: React.PointerEvent) => {
      const start = from.current;
      if (!start) return;
      onChange(boxed(start.height + (event.clientY - start.y)));
    },
    [onChange],
  );

  return (
    <div
      role="separator"
      aria-label="Drag to resize the canvas"
      aria-orientation="horizontal"
      className="absolute inset-x-0 bottom-0 h-2 cursor-ns-resize touch-none after:absolute after:inset-x-0 after:bottom-0 after:h-px after:bg-transparent hover:after:bg-border"
      onPointerDown={(event) => {
        from.current = { y: event.clientY, height };
        event.currentTarget.setPointerCapture(event.pointerId);
      }}
      onPointerMove={move}
      onPointerUp={() => {
        from.current = null;
      }}
    />
  );
}

/**
 * The whole window.
 *
 * A second frame rather than the same one moved, because a null-origin document cannot be
 * reparented without reloading anyway — so this is the same cost, with the inline canvas left
 * running underneath where you left it.
 *
 * Through a portal to `<body>` for the reason the diagram's lightbox documents at length: the
 * message this sits inside carries `content-visibility: auto`, which makes it the containing
 * block for `position: fixed`, and "full screen" otherwise comes out the size of the message.
 *
 * A second frame means a second load, so the page comes up at its defaults rather than where you
 * left it — the same thing that already happens visibly, now also true of what gets reported.
 * That is the honest version: what he is told about a canvas is what is on the screen.
 */
function Lightbox({
  doc,
  dark,
  onMessage,
  onClose,
}: {
  doc: string;
  dark: boolean;
  onMessage: (message: ReturnType<typeof readCanvasMessage>) => void;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Canvas"
      className="bg-background fixed inset-0 z-50 overflow-hidden"
    >
      <Frame doc={doc} dark={dark} onMessage={onMessage} className="size-full" />
      <div className="absolute end-4 top-4 flex items-center gap-1">
        <OverlayButton label="Close" onClick={onClose}>
          <X className="size-3.5" />
        </OverlayButton>
      </div>
    </div>,
    document.body,
  );
}
