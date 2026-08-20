import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, Check, Copy, Maximize2, Minimize2, X, ZoomIn, ZoomOut } from "lucide-react";

import { useAuiState } from "@assistant-ui/react";
import type { SyntaxHighlighterProps } from "@assistant-ui/react-markdown";

import { AnimatedDiagram } from "@/components/assistant-ui/animated-diagram";
import { OverlayButton } from "@/components/assistant-ui/overlay-button";
import { naturalSize, toPng } from "@/lib/diagram";
import { hasFlowScript } from "@/lib/flow-script";
import { PALETTE } from "@/lib/kith-palette";
import { mermaidConfig } from "@/lib/mermaid-config";
import { useDarkMode } from "@/lib/theme";
import { cn } from "@/lib/utils";

/**
 * A ```mermaid block, drawn.
 *
 * The reason this is mermaid rather than something nicer to write is not that mermaid is
 * nice to write. It is what the model already emits, unprompted, when it wants to show a
 * shape — so anything else means either instructing him to use a second syntax he will keep
 * forgetting, or translating one DSL into another and owning the translator. The complaint
 * was never the language; it was that a diagram arrived as thirty lines of source you had to
 * read like code. So: same language, drawn, and made to look like it belongs in this app.
 *
 * Three things make it feel embedded rather than bolted on, and each is a real problem:
 *
 * **It streams.** The fence arrives a token at a time, so most of the renders this component
 * does are of a fragment — `graph TD\n  A[Sta`. Mermaid throws on those. It would be wrong to
 * show the throw and wrong to show nothing, so a fragment keeps the *last drawing that
 * worked* on screen and only the first one has a placeholder. The diagram fills in as he
 * writes it instead of appearing all at once at the end.
 *
 * **A failure is not an error message.** If the code settles and still will not parse, he
 * wrote invalid mermaid — and the useful thing then is the source, which is exactly what the
 * block would have shown before any of this existed. That is what `Pre`/`Code` are handed to
 * us for. No red box; you just get the code back.
 *
 * **It is the app's palette, not mermaid's.** Default mermaid is lilac on white with Trebuchet
 * MS, which reads as a wiki plugin from 2014 dropped into the middle of a conversation.
 */

/** Loaded once, on the first diagram anyone sees.
 *
 *  Mermaid is about a megabyte of parser and layout engine, and most conversations contain no
 *  diagram at all — a static import would put it in the entry chunk of every session that
 *  never draws one. The promise is module-level so a message with six diagrams still loads it
 *  once rather than six times. */
let engine: Promise<typeof import("mermaid")["default"]> | null = null;

function mermaidEngine() {
  engine ??= import("mermaid").then((mod) => mod.default);
  return engine;
}

/** Ids have to be unique per render or mermaid reuses a stale `<defs>` for the arrowheads —
 *  which shows up as every edge after the first losing its arrow. */
let seq = 0;


/** The assistant-ui contract: a fenced block in a streaming message.
 *
 *  Thin on purpose. What it supplies is the fallback — the ordinary code block, built from the
 *  `Pre`/`Code` the library hands over — so an invalid diagram degrades to exactly what that
 *  fence rendered as before this component existed.
 *
 *  And which of the two renderers draws it, which is one predicate: a `flow:` key in mermaid's
 *  frontmatter is him asking for the diagram to move. Everything else about the fence is the
 *  same, including this one — the animated path is handed the still diagram to show while the
 *  reply is still arriving, and to keep showing if the choreography turns out not to compile. */
export function MermaidBlock({ code, components: { Pre, Code } }: SyntaxHighlighterProps) {
  /* Whether anything is still being written, asked of the thread rather than guessed from the
     text. The still renderer does not need to know — a fragment of mermaid is a parse failure and
     it keeps the last drawing that worked — but the animator renders in one go and cannot be
     given half a diagram, so this is what tells it to wait.

     The *thread*, not this message. A turn is many messages — he draws a diagram in the middle
     of a long answer and keeps writing for another minute — and a diagram that starts moving
     while the paragraphs under it are still arriving is a distraction from the thing being
     read. It waits for the whole turn to land. The message's own status is still checked, for
     the reloaded-mid-turn case where the thread is idle and a message is not. */
  const streaming = useAuiState(
    (state) => state.thread.isRunning || state.message.status?.type === "running",
  );
  const fallback = (
    <Pre>
      <Code>{code}</Code>
    </Pre>
  );
  if (!hasFlowScript(code)) return <MermaidDiagram code={code} fallback={fallback} />;
  return (
    <AnimatedDiagram
      code={code}
      still={<MermaidDiagram code={code} fallback={fallback} bare />}
      streaming={streaming}
    />
  );
}

export function MermaidDiagram({
  code,
  fallback,
  bare = false,
}: {
  code: string;
  fallback: ReactNode;
  /** Just the drawing: no card, no padding, no toolbar of its own.
   *
   *  For the animated fence, which shows this same diagram before its animation is built and
   *  again after it has finished, inside a figure that already has a border and buttons. Left to
   *  itself it drew a bordered card inside a bordered card, and swapping between the two moved
   *  the page by the height of the padding it added. */
  bare?: boolean;
}) {
  const dark = useDarkMode();
  const [svg, setSvg] = useState("");
  /** Only true once the code has stopped changing *and* still will not parse. Until then a
   *  failure is almost certainly a half-written diagram. */
  const [broken, setBroken] = useState(false);
  const [zoomed, setZoomed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    // The settle window. Long enough that it never fires between two tokens of the same
    // diagram, short enough that a genuinely broken one gives up its source promptly.
    const timer = setTimeout(async () => {
      const source = code.trim();
      if (!source) return;
      try {
        const mermaid = await mermaidEngine();
        mermaid.initialize(mermaidConfig(dark));
        // Parses first so a fragment never reaches the renderer — `render` on bad input leaves
        // an orphaned `#d…` element in the body, and enough of them is a visible pile of
        // half-drawn diagrams at the bottom of the window.
        const ok = await mermaid.parse(source, { suppressErrors: true });
        if (cancelled) return;
        if (!ok) {
          setBroken(true);
          return;
        }
        const { svg: drawn } = await mermaid.render(`kith-diagram-${(seq += 1)}`, source);
        if (cancelled) return;
        setSvg(drawn);
        setBroken(false);
      } catch {
        if (!cancelled) setBroken(true);
      }
    }, 220);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [code, dark]);

  // Settled and unparseable: he wrote invalid mermaid, and the source is the useful thing.
  // Exactly what the block showed before any of this existed.
  // Pinned to the viewBox once per render, not on every paint.
  const sized = useMemo(() => (svg ? naturalSize(svg).html : ""), [svg]);

  if (broken && !svg) return <>{fallback}</>;

  if (bare) {
    return svg ? (
      <div
        className="flex justify-center overflow-x-auto [&_svg]:h-auto [&_svg]:max-w-full!"
        dangerouslySetInnerHTML={{ __html: sized }}
      />
    ) : (
      <Drawing />
    );
  }

  return (
    <>
      <figure
        data-slot="kith_mermaid"
        className="group border-border/60 bg-card/40 relative my-3 overflow-hidden rounded-xl border"
      >
        {svg ? (
          <>
            {/* Its own size, and never more than that.
                Mermaid emits `width="100%"`, so the SVG stretched to whatever the column was.
                For anything narrow — a straight vertical chain, which is most diagrams he
                draws — that is a magnification: a 200px-wide graph blown to 1200px is six
                times, so 14px labels became 80px and two nodes filled three screens.
                `max-w-full` could not save it, because 100% *is* full.

                `naturalSize` pins width and height to the viewBox, and the CSS only ever takes
                size away: `max-w-full` shrinks something genuinely wider than the column, and
                `h-auto` keeps that in proportion. So a small diagram stays small, a wide one
                fits, and nothing is ever drawn larger than it was laid out to be — which is
                the rule the Lightbox has always used ("never magnified past 1"). */}
            <div
              className="flex justify-center overflow-x-auto p-4 [&_svg]:h-auto [&_svg]:max-w-full!"
              // Mermaid's output — built by mermaid from the code he wrote, in a renderer that
              // already runs his shell commands. Rendered rather than escaped because an SVG
              // shown as text is the thing this component exists to stop doing.
              dangerouslySetInnerHTML={{ __html: sized }}
            />
            <Toolbar svg={svg} dark={dark} onZoom={() => setZoomed(true)} />
          </>
        ) : (
          <Drawing />
        )}
      </figure>
      {zoomed ? <Lightbox svg={svg} onClose={() => setZoomed(false)} /> : null}
    </>
  );
}

/** The first one only. Every diagram after it keeps the previous drawing while it re-renders,
 *  so the picture never blinks out mid-sentence. */
function Drawing() {
  return (
    <div className="text-muted-foreground/50 flex items-center gap-2 px-4 py-8 font-mono text-[11px]">
      <span className="bg-muted-foreground/40 size-1.5 animate-pulse rounded-full" />
      drawing…
    </div>
  );
}

function Toolbar({ svg, dark, onZoom }: { svg: string; dark: boolean; onZoom: () => void }) {
  /** Deliberately three-valued. A copy that silently failed and showed a tick is a lie the
   *  person only discovers when they paste — this app has made that mistake before. */
  const [state, setState] = useState<"idle" | "done" | "failed">("idle");

  const copy = useCallback(async () => {
    const settle = (next: "done" | "failed") => {
      setState(next);
      setTimeout(() => setState("idle"), 2200);
    };
    try {
      const png = await toPng(svg, PALETTE[dark ? "dark" : "light"].background);
      if (!png) return settle("failed");
      await navigator.clipboard.write([new ClipboardItem({ "image/png": png })]);
      settle("done");
    } catch {
      settle("failed");
    }
  }, [svg, dark]);

  return (
    // Hidden until the diagram is hovered — two buttons parked on every picture is clutter,
    // and clutter is the thing being fixed here. Kept reachable by keyboard regardless.
    <div className="absolute end-2 top-2 flex gap-1 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100">
      <OverlayButton
        label={state === "failed" ? "Could not copy the diagram" : "Copy the diagram as an image"}
        onClick={() => void copy()}
      >
        {state === "done" ? (
          <Check className="size-3.5" />
        ) : state === "failed" ? (
          <AlertTriangle className="text-destructive size-3.5" />
        ) : (
          <Copy className="size-3.5" />
        )}
      </OverlayButton>
      <OverlayButton label="Open the diagram full screen" onClick={onZoom}>
        <Maximize2 className="size-3.5" />
      </OverlayButton>
    </div>
  );
}

const ZOOM_RANGE = [0.1, 8] as const;
const clamp = (k: number) => Math.min(ZOOM_RANGE[1], Math.max(ZOOM_RANGE[0], k));

/** The whole window, and you can move around in it.
 *
 *  A thirty-node flowchart is not legible at any single scale on any screen: fitted to the
 *  window the labels are four pixels tall, and at reading size it is three screens wide. So the
 *  answer is not a bigger box, it is being able to go and look — fit to start, then wheel to
 *  zoom about the pointer and drag to pan, which is what every other diagram surface does and
 *  therefore the thing nobody has to be taught. */
function Lightbox({ svg, onClose }: { svg: string; onClose: () => void }) {
  const { html, width, height } = useMemo(() => naturalSize(svg), [svg]);
  const surface = useRef<HTMLDivElement>(null);
  const [view, setView] = useState({ k: 1, x: 0, y: 0 });
  const [grabbing, setGrabbing] = useState(false);
  const pan = useRef<{ px: number; py: number; x: number; y: number } | null>(null);

  /** Centred, and scaled so the whole thing is on screen. Never magnified past 1: a four-node
   *  diagram blown up to fill a 27" display looks like a mistake. */
  const fit = useCallback(() => {
    const el = surface.current;
    if (!el || !width || !height) return;
    const pad = 56;
    const k = clamp(
      Math.min((el.clientWidth - pad * 2) / width, (el.clientHeight - pad * 2) / height, 1),
    );
    setView({ k, x: (el.clientWidth - width * k) / 2, y: (el.clientHeight - height * k) / 2 });
  }, [width, height]);

  useLayoutEffect(fit, [fit]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key === "0") fit();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, fit]);

  // Attached by hand because React registers `wheel` passively, and a passive listener cannot
  // call `preventDefault` — so the trackpad would zoom the diagram *and* scroll whatever is
  // behind the overlay at the same time.
  useEffect(() => {
    const el = surface.current;
    if (!el) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = el.getBoundingClientRect();
      const px = event.clientX - rect.left;
      const py = event.clientY - rect.top;
      setView((v) => {
        const k = clamp(v.k * Math.exp(-event.deltaY / 400));
        // Hold the point under the cursor still. Scaling about the origin instead is what makes
        // a zoom feel like the diagram is running away from you.
        const ratio = k / v.k;
        return { k, x: px - (px - v.x) * ratio, y: py - (py - v.y) * ratio };
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  const nudge = (factor: number) =>
    setView((v) => {
      const el = surface.current;
      if (!el) return v;
      const px = el.clientWidth / 2;
      const py = el.clientHeight / 2;
      const k = clamp(v.k * factor);
      const ratio = k / v.k;
      return { k, x: px - (px - v.x) * ratio, y: py - (py - v.y) * ratio };
    });

  // Through a portal to `<body>`, and that is not tidiness — it is the only way this covers the
  // window. The assistant message it lives inside carries `content-visibility: auto`, which
  // implies `contain: paint`, and a painted-contained element is a containing block for
  // `position: fixed` descendants. So `inset-0` resolved against the *message* rather than the
  // viewport: "full screen" came out the size of the message body, clipped, with three pages of
  // reply to scroll through to find it.
  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Diagram"
      className="bg-background fixed inset-0 z-50 overflow-hidden"
    >
      <div
        ref={surface}
        onPointerDown={(event) => {
          pan.current = { px: event.clientX, py: event.clientY, x: view.x, y: view.y };
          setGrabbing(true);
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
          const from = pan.current;
          if (!from) return;
          setView((v) => ({
            ...v,
            x: from.x + (event.clientX - from.px),
            y: from.y + (event.clientY - from.py),
          }));
        }}
        onPointerUp={() => {
          pan.current = null;
          setGrabbing(false);
        }}
        onDoubleClick={fit}
        className={cn("absolute inset-0 touch-none", grabbing ? "cursor-grabbing" : "cursor-grab")}
      >
        <div
          className="absolute top-0 left-0 origin-top-left will-change-transform"
          style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})` }}
          dangerouslySetInnerHTML={{ __html: html }}
        />
      </div>

      <div className="absolute end-4 top-4 flex items-center gap-1">
        <span className="text-muted-foreground/60 me-1 font-mono text-[11px] tabular-nums">
          {Math.round(view.k * 100)}%
        </span>
        <OverlayButton label="Zoom out" onClick={() => nudge(1 / 1.3)}>
          <ZoomOut className="size-3.5" />
        </OverlayButton>
        <OverlayButton label="Zoom in" onClick={() => nudge(1.3)}>
          <ZoomIn className="size-3.5" />
        </OverlayButton>
        <OverlayButton label="Fit to the window" onClick={fit}>
          <Minimize2 className="size-3.5" />
        </OverlayButton>
        <OverlayButton label="Close" onClick={onClose}>
          <X className="size-3.5" />
        </OverlayButton>
      </div>
    </div>,
    document.body,
  );
}
