import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, Check, Copy, Maximize2, Minimize2, X, ZoomIn, ZoomOut } from "lucide-react";

import type { SyntaxHighlighterProps } from "@assistant-ui/react-markdown";

import { naturalSize, toPng } from "@/lib/diagram";
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

/**
 * The app's own tokens, resolved.
 *
 * These mirror `--background`, `--foreground`, `--muted`, `--border`, `--kith` and `--roam`
 * from `index.css`, converted out of oklch because mermaid derives shades from what it is
 * given (khroma darkens and lightens these to build the rest of its palette) and does that
 * arithmetic on colours it can parse. Baked rather than read from the live stylesheet: reading
 * means resolving a custom property to something mermaid will accept, and getting a usable
 * value back out of `getComputedStyle` for an oklch token is browser-dependent in a way that
 * fails silently and looks like a theming bug.
 *
 * If the tokens in `index.css` change, these want changing with them.
 */
const PALETTE = {
  light: {
    background: "#fbf9f5",
    surface: "#fffefb",
    line: "#e4e0dc",
    text: "#29231d",
    dim: "#69625b",
    accent: "#c77618",
    accentSoft: "#f4e7d6",
    second: "#0e8c41",
    secondSoft: "#daeadc",
    muted: "#f1eee9",
  },
  dark: {
    background: "#110e0b",
    surface: "#1a1713",
    line: "#25221e",
    text: "#eae5dd",
    dim: "#a19a92",
    accent: "#f1b65a",
    accentSoft: "#352918",
    second: "#5dd786",
    secondSoft: "#1d2e1f",
    muted: "#26221f",
  },
} as const;

/** The `base` theme with every colour it derives from replaced.
 *
 *  `base` rather than `dark`/`neutral` because it is the only one mermaid means to be
 *  overridden — the named themes hardcode shades that ignore half of what you pass. */
function themeVariables(dark: boolean) {
  const c = PALETTE[dark ? "dark" : "light"];
  return {
    darkMode: dark,
    background: "transparent",
    fontFamily:
      'ui-sans-serif, -apple-system, "SF Pro Text", "Segoe UI", system-ui, sans-serif',
    fontSize: "14px",

    // Nodes. A filled box in the accent's soft tint with a solid accent edge, which is the
    // same treatment the rest of the app gives something it wants you to look at.
    primaryColor: c.accentSoft,
    primaryTextColor: c.text,
    primaryBorderColor: c.accent,
    secondaryColor: c.muted,
    secondaryTextColor: c.text,
    secondaryBorderColor: c.line,
    tertiaryColor: c.secondSoft,
    tertiaryTextColor: c.text,
    tertiaryBorderColor: c.second,

    mainBkg: c.accentSoft,
    secondBkg: c.muted,
    lineColor: c.dim,
    textColor: c.text,
    border1: c.accent,
    border2: c.line,
    nodeBorder: c.accent,
    nodeTextColor: c.text,
    clusterBkg: "transparent",
    clusterBorder: c.line,
    titleColor: c.text,
    edgeLabelBackground: c.background,

    // Sequence diagrams.
    actorBkg: c.accentSoft,
    actorBorder: c.accent,
    actorTextColor: c.text,
    actorLineColor: c.line,
    signalColor: c.text,
    signalTextColor: c.text,
    labelBoxBkgColor: c.accentSoft,
    labelBoxBorderColor: c.accent,
    labelTextColor: c.text,
    loopTextColor: c.text,
    noteBkgColor: c.secondSoft,
    noteBorderColor: c.second,
    noteTextColor: c.text,
    activationBkgColor: c.muted,
    activationBorderColor: c.line,
    sequenceNumberColor: c.background,

    // State and class diagrams.
    labelColor: c.text,
    altBackground: c.muted,

    // Gantt.
    sectionBkgColor: c.muted,
    sectionBkgColor2: c.background,
    altSectionBkgColor: c.background,
    gridColor: c.line,
    todayLineColor: c.accent,
    taskBkgColor: c.accentSoft,
    taskBorderColor: c.accent,
    taskTextColor: c.text,
    taskTextOutsideColor: c.text,
    taskTextLightColor: c.text,
    taskTextDarkColor: c.text,
    doneTaskBkgColor: c.muted,
    doneTaskBorderColor: c.line,
    activeTaskBkgColor: c.secondSoft,
    activeTaskBorderColor: c.second,
    critBorderColor: c.accent,
    critBkgColor: c.accentSoft,

    // Pie and quadrant, which otherwise reach for their own unrelated palette.
    pie1: c.accent,
    pie2: c.second,
    pie3: c.dim,
    pie4: c.accentSoft,
    pie5: c.secondSoft,
    pie6: c.muted,
    pieTitleTextColor: c.text,
    pieSectionTextColor: c.text,
    pieLegendTextColor: c.dim,
    pieStrokeColor: c.background,
    pieOuterStrokeColor: c.line,
  };
}

/** Ids have to be unique per render or mermaid reuses a stale `<defs>` for the arrowheads —
 *  which shows up as every edge after the first losing its arrow. */
let seq = 0;


/** The assistant-ui contract: a fenced block in a streaming message.
 *
 *  Thin on purpose. What it supplies is the fallback — the ordinary code block, built from the
 *  `Pre`/`Code` the library hands over — so an invalid diagram degrades to exactly what that
 *  fence rendered as before this component existed. */
export function MermaidBlock({ code, components: { Pre, Code } }: SyntaxHighlighterProps) {
  return (
    <MermaidDiagram
      code={code}
      fallback={
        <Pre>
          <Code>{code}</Code>
        </Pre>
      }
    />
  );
}

export function MermaidDiagram({ code, fallback }: { code: string; fallback: ReactNode }) {
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
        mermaid.initialize({
          startOnLoad: false,
          // He is writing this, not a person — and it renders in a desktop app that already
          // trusts him with a shell. `loose` is what lets `click` bindings and HTML labels
          // work at all, and refusing them would only mean diagrams that quietly lose half
          // their labels.
          securityLevel: "loose",
          theme: "base",
          themeVariables: themeVariables(dark),
          // `htmlLabels: false` makes every label a real `<text>` rather than a `<foreignObject>`
          // wrapping HTML. That is what makes the drawing *portable*: Chromium refuses to
          // rasterise a foreignObject inside an SVG image, so with HTML labels the copied PNG
          // comes out as a picture of the arrows with every word missing. The cost is mermaid's
          // cleverer label wrapping, which is a fair trade for a diagram you can paste.
          htmlLabels: false,
          flowchart: { curve: "basis", padding: 14, useMaxWidth: true, htmlLabels: false },
          sequence: { useMaxWidth: true, actorMargin: 40 },
          gantt: { useMaxWidth: true },
        });
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
      <IconButton
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
      </IconButton>
      <IconButton label="Open the diagram full screen" onClick={onZoom}>
        <Maximize2 className="size-3.5" />
      </IconButton>
    </div>
  );
}

function IconButton({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className="border-border/60 bg-card/80 text-muted-foreground hover:text-foreground flex size-7 items-center justify-center rounded-md border backdrop-blur-sm transition-colors"
    >
      {children}
    </button>
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
        <IconButton label="Zoom out" onClick={() => nudge(1 / 1.3)}>
          <ZoomOut className="size-3.5" />
        </IconButton>
        <IconButton label="Zoom in" onClick={() => nudge(1.3)}>
          <ZoomIn className="size-3.5" />
        </IconButton>
        <IconButton label="Fit to the window" onClick={fit}>
          <Minimize2 className="size-3.5" />
        </IconButton>
        <IconButton label="Close" onClick={onClose}>
          <X className="size-3.5" />
        </IconButton>
      </div>
    </div>,
    document.body,
  );
}
