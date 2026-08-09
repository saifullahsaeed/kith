import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Check, Copy, Maximize2, X } from "lucide-react";

import type { SyntaxHighlighterProps } from "@assistant-ui/react-markdown";

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
          flowchart: { curve: "basis", padding: 14, useMaxWidth: true },
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
  if (broken && !svg) return <>{fallback}</>;

  return (
    <>
      <figure
        data-slot="kith_mermaid"
        className="group border-border/60 bg-card/40 relative my-3 overflow-hidden rounded-xl border"
      >
        {svg ? (
          <>
            <div
              className="flex justify-center overflow-x-auto p-4 [&_svg]:h-auto [&_svg]:max-w-full!"
              // Mermaid's output — built by mermaid from the code he wrote, in a renderer that
              // already runs his shell commands. Rendered rather than escaped because an SVG
              // shown as text is the thing this component exists to stop doing.
              dangerouslySetInnerHTML={{ __html: svg }}
            />
            <Toolbar code={code} onZoom={() => setZoomed(true)} />
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

function Toolbar({ code, onZoom }: { code: string; onZoom: () => void }) {
  const [copied, setCopied] = useState(false);
  const copy = useCallback(() => {
    void navigator.clipboard?.writeText(code).then(
      () => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      },
      () => {},
    );
  }, [code]);

  return (
    // Hidden until the diagram is hovered — two buttons parked on every picture is clutter,
    // and clutter is the thing being fixed here. Kept reachable by keyboard regardless.
    <div className="absolute end-2 top-2 flex gap-1 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100">
      <IconButton label="Copy the diagram source" onClick={copy}>
        {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
      </IconButton>
      <IconButton label="Open the diagram larger" onClick={onZoom}>
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

/** The same drawing, given the window.
 *
 *  A flowchart with twenty nodes is legible at conversation width or it is not, and when it is
 *  not there is nothing to be done inside a 700px column. Scrollable rather than zoomable: the
 *  SVG is vector, so the browser's own zoom is sharper than anything a transform would do here,
 *  and a pan-and-zoom surface is a lot of interaction to maintain for "let me see the corner". */
function Lightbox({ svg, onClose }: { svg: string; onClose: () => void }) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Diagram"
      onClick={onClose}
      className="bg-background/80 fixed inset-0 z-50 flex items-center justify-center p-8 backdrop-blur-sm"
    >
      <div
        onClick={(event) => event.stopPropagation()}
        className={cn(
          "border-border/60 bg-card relative max-h-full max-w-full overflow-auto rounded-2xl border p-8 shadow-2xl",
        )}
      >
        <div
          className="[&_svg]:h-auto [&_svg]:max-w-none"
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      </div>
      <button
        type="button"
        onClick={onClose}
        aria-label="Close"
        className="border-border/60 bg-card/80 text-muted-foreground hover:text-foreground absolute end-4 top-4 flex size-8 items-center justify-center rounded-lg border"
      >
        <X className="size-4" />
      </button>
    </div>
  );
}
