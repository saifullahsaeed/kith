import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { Code2, Maximize2, RotateCw, X } from "lucide-react";

import type { SyntaxHighlighterProps } from "@assistant-ui/react-markdown";

import { OverlayButton } from "@/components/assistant-ui/overlay-button";
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
const HEIGHT_RANGE = [160, 1200] as const;

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
  return (
    <HtmlCanvas
      code={code}
      fallback={
        <Pre>
          <Code>{code}</Code>
        </Pre>
      }
    />
  );
}

export function HtmlCanvas({ code, fallback }: { code: string; fallback: ReactNode }) {
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
  /** Bumped by the replay button, and used as the frame's key — remounting is the only way to
   *  restart a page whose animation we are not allowed to talk to. */
  const [generation, setGeneration] = useState(0);

  // Both synchronous, so a canvas shows a placeholder from its first token rather than a flash
  // of source that turns into a drawing 220ms later. They are regexes; they can afford to run on
  // every render.
  const renderable = useMemo(() => looksRenderable(code), [code]);
  const complete = useMemo(() => isComplete(code), [code]);

  useEffect(() => {
    if (!renderable) return;
    let cancelled = false;
    const build = setTimeout(() => {
      if (!isComplete(code)) return;
      void host(sealedDocument(code, paletteFor(dark))).then((url) => {
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
  }, [code, dark, renderable]);

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
          <Frame doc={doc} generation={generation} height={height} />
        ) : (
          <Building />
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

        {!showSource && doc ? <ResizeHandle height={height} onChange={setHeight} /> : null}
      </figure>
      {zoomed ? <Lightbox doc={doc} onClose={() => setZoomed(false)} /> : null}
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
 * `key` on the generation rather than a `src` change: reassigning `srcDoc` to the same string is
 * a no-op, so replay has to be a remount.
 */
function Frame({ doc, generation, height }: { doc: string; generation: number; height: number }) {
  return (
    <iframe
      key={generation}
      title="Canvas"
      sandbox={FRAME_SANDBOX}
      src={doc}
      className="block w-full border-0 bg-transparent"
      style={{ height }}
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

/** Shown while the document is still arriving. Deliberately not the source: a canvas that
 *  flashed its own code before drawing would undo the thing this component is for. */
function Building() {
  return (
    <div className="text-muted-foreground/50 flex items-center gap-2 px-4 py-8 font-mono text-[11px]">
      <span className="bg-muted-foreground/40 size-1.5 animate-pulse rounded-full" />
      building…
    </div>
  );
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
      const next = start.height + (event.clientY - start.y);
      onChange(Math.min(HEIGHT_RANGE[1], Math.max(HEIGHT_RANGE[0], next)));
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
 */
function Lightbox({ doc, onClose }: { doc: string; onClose: () => void }) {
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
      <iframe
        title="Canvas"
        sandbox={FRAME_SANDBOX}
        src={doc}
        className="size-full border-0 bg-transparent"
      />
      <div className="absolute end-4 top-4 flex items-center gap-1">
        <OverlayButton label="Close" onClick={onClose}>
          <X className="size-3.5" />
        </OverlayButton>
      </div>
    </div>,
    document.body,
  );
}
