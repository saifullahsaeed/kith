import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, Check, Copy, Maximize2, Pause, Play, X } from "lucide-react";

import type { MermaidAnimator, SceneMarker } from "mermaid-animator";

import { OverlayButton } from "@/components/assistant-ui/overlay-button";
import { animatorTheme } from "@/lib/animator-theme";
import { toPng } from "@/lib/diagram";
import { PALETTE } from "@/lib/kith-palette";
import { mermaidOptions } from "@/lib/mermaid-config";
import { useDarkMode } from "@/lib/theme";
import { cn } from "@/lib/utils";

/**
 * A ```mermaid block that moves.
 *
 * The still diagram earned its place by being the language he already writes. This is the same
 * argument taken one step: a flow script is a `flow:` key in mermaid's own frontmatter, so a
 * fence that animates is still a mermaid fence — it draws as an ordinary diagram everywhere the
 * animator is not, and he does not have to hold a second syntax in mind to reach for it.
 *
 * What it adds is the thing a still picture cannot do: *which way the thing went*. A request
 * path, a failover, a retry, a queue draining — those are all one diagram plus an order of
 * events, and reading the order out of an arrow soup is work the drawing should have done.
 *
 * Four decisions here are load-bearing, and each is a failure it would otherwise have:
 *
 * **It draws still first, and comes alive at the end.** The animator renders the diagram in one
 * go and needs a whole one, so it cannot be fed a fragment the way the still renderer happily
 * is. So the still renderer does the streaming — the diagram fills in as he writes it, exactly
 * as before — and the animation replaces it once the tokens stop. Nothing about a half-written
 * animated fence looks different from a half-written plain one.
 *
 * **A broken flow script loses the animation, not the diagram.** The script is validated against
 * the diagram's own nodes and edges before anything is built, and a script that names a node
 * that is not there, or routes through a pair with no edge between them, leaves the still
 * drawing standing and says why in one line underneath. The reply is not a red box, and the
 * picture is not lost to a typo in its choreography.
 *
 * **Inline, it does not pan and does not zoom.** The animator's wheel handler calls
 * `preventDefault` unconditionally, so leaving zoom on would mean the conversation stops
 * scrolling whenever the pointer crosses a diagram. Full screen is where you go to look around,
 * which is the same division the still diagram already has.
 *
 * **It stops when you are not looking at it.** A flow script loops forever. Ten replies down a
 * long conversation, that is ten animation loops running against a renderer that has been
 * frozen by less. Off screen, or covered by the full-screen view, it pauses.
 */

/** How long the code has to stop changing before the animation is built. The still diagram's
 *  settle window, for the same reason — long enough never to fire between two tokens. */
const SETTLE_MS = 220;

/** What a box that has not measured itself yet is worth. Only ever on screen for the frame
 *  between the animator rendering and reading its own height back. */
const DEFAULT_HEIGHT = 380;

/** How tall the box in the conversation may be, however it got that way. The same bounds the
 *  canvas uses, and the same reason for going through one function: a drag has to continue from
 *  a size the drag itself could have reached. */
const BOX_HEIGHT = [160, 1200] as const;

const boxed = (px: number) => Math.min(BOX_HEIGHT[1], Math.max(BOX_HEIGHT[0], px));

/** Loaded on the first animated diagram anyone sees, and never twice.
 *
 *  A separate chunk from mermaid itself, which most conversations already do not load. The
 *  promise is module-level so a reply with three animated diagrams fetches it once. */
let library: Promise<typeof import("mermaid-animator")> | null = null;

function animatorLibrary() {
  library ??= import("mermaid-animator");
  return library;
}

/** Everything the animator is told, minus what only the caller knows.
 *
 *  `mermaid` is `mermaidConfig` rather than the animator's own defaults, and it is spread after
 *  them inside the library, so a moving diagram and a still one are drawn by a mermaid holding
 *  one opinion about Kith's colours. See `lib/mermaid-config.ts`. */
function options(dark: boolean, explorable: boolean) {
  return {
    theme: animatorTheme(dark),
    mermaid: mermaidOptions(dark),
    pan: explorable,
    zoom: explorable,
    inspect: true,
    /** A packet has to be findable on a diagram the width of a reply. */
    dotRadius: 4,
  };
}

/**
 * One animator, mounted on a box.
 *
 * Shared by the inline diagram and the full-screen one, which differ only in whether you can
 * drag the drawing around and where they start from. Holding it in a hook rather than a
 * component is what lets the inline box keep the still diagram on top of itself until the
 * animation is genuinely ready to replace it — there is no arrangement of two components that
 * gives one of them a laid-out container it is not yet allowed to show.
 */
function useAnimator({
  code,
  dark,
  explorable,
  enabled,
  delay,
  startAt,
}: {
  code: string;
  dark: boolean;
  explorable: boolean;
  /** False while the reply is still being written. */
  enabled: boolean;
  delay: number;
  /** Where to open, in ms. The full-screen view starts where the inline one had got to. */
  startAt?: number;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [animator, setAnimator] = useState<MermaidAnimator | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** Where the last animator had got to, so that switching theme — which has to rebuild it,
   *  since the palette is baked in at render — does not send the animation back to zero.
   *  Rendering is a pure function of time, so this is exact rather than approximate. */
  const resumeAt = useRef(startAt ?? 0);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let built: MermaidAnimator | null = null;

    const timer = setTimeout(() => {
      void (async () => {
        const container = box.current;
        if (!container) return;
        try {
          const { MermaidAnimator, validateFlowInDiagram } = await animatorLibrary();
          if (cancelled) return;
          const theme = animatorTheme(dark);
          // Checked before anything is drawn, and against the diagram rather than only against
          // the schema: the two mistakes worth catching — a route naming a node that is not
          // there, and a hop with no edge under it — are both invisible to a validator that has
          // not read the graph. Told about this theme's own colour and state names, or every
          // name Kith adds to the palette would come back as an unknown one.
          const check = await validateFlowInDiagram(code, {
            colors: Object.keys(theme.flowColors ?? {}),
            states: Object.keys(theme.states ?? {}),
          });
          if (cancelled) return;
          if (!check.ok) {
            setError(check.line ? `${check.message} (line ${check.line})` : check.message);
            return;
          }
          built = await MermaidAnimator.create(container, code, options(dark, explorable));
          if (cancelled) {
            built.destroy();
            return;
          }
          if (resumeAt.current) built.seek(resumeAt.current);
          setError(null);
          setAnimator(built);
        } catch (thrown) {
          if (cancelled) return;
          // Anything the validator could not know: a diagram type whose graph it cannot read, a
          // route that only fails once the edges are real geometry, a mermaid parse that got
          // this far and then would not lay out.
          setError(thrown instanceof Error ? thrown.message : String(thrown));
        }
      })();
    }, delay);

    return () => {
      cancelled = true;
      clearTimeout(timer);
      if (built) {
        resumeAt.current = built.currentTime();
        built.destroy();
      }
      setAnimator(null);
    };
  }, [code, dark, explorable, enabled, delay]);

  return { box, animator, error };
}

export function AnimatedDiagram({
  code,
  still,
  streaming = false,
}: {
  code: string;
  /** The same fence as an ordinary still diagram. On screen until the animation is up, and for
   *  good if it never is — so a flow script that will not compile costs the choreography and
   *  nothing else. Passed in rather than imported to keep the two renderers from importing each
   *  other. */
  still: ReactNode;
  streaming?: boolean;
}) {
  const dark = useDarkMode();
  const [zoomed, setZoomed] = useState(false);
  const [height, setHeight] = useState<number | null>(null);
  /** Set the moment the bottom edge is dragged, and never unset: a box that re-measured itself
   *  after you had chosen a size would undo the choice. */
  const [sized, setSized] = useState(false);
  const [wanted, setWanted] = useState(true);
  const [visible, setVisible] = useState(true);

  const { box, animator, error } = useAnimator({
    code,
    dark,
    explorable: false,
    enabled: !streaming,
    delay: SETTLE_MS,
  });

  /* Its own height, and never more than that.
     The animator makes the drawing fill its container — width and height both 100%, its own
     attributes stripped — so unlike the still renderer it has no intrinsic size to pin. Left at
     a fixed height a four-node diagram would be scaled up to fill it, which is the "expand made
     it smaller" bug wearing the other hat, and the rule this app already follows is that CSS
     may only ever take size away.

     The viewBox height is the size mermaid laid the diagram out at, so a box that tall makes the
     fit scale exactly 1 — the drawing at its own size, centred — and anything past the ceiling
     is shrunk to fit rather than cropped. */
  useEffect(() => {
    if (!animator || sized) return;
    const drawn = box.current?.querySelector("svg");
    const natural = drawn?.viewBox.baseVal.height ?? 0;
    setHeight(boxed(natural > 0 ? Math.round(natural) : DEFAULT_HEIGHT));
  }, [animator, sized, box]);

  /* A loop nobody is watching. `content-visibility: auto` on the message means an off-screen
     diagram is not even painted, but its callbacks still run and still write to the DOM, so
     this is the difference between one animation running and every animation in the
     conversation running. */
  useEffect(() => {
    const el = box.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      ([entry]) => setVisible(entry.isIntersecting),
      // A little ahead of the scroll, so it is already moving by the time it is on screen.
      { rootMargin: "200px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [box]);

  /* One place decides whether the animation is running, from the three things that can stop it:
     you paused it, you scrolled away from it, or you opened it full screen and the inline copy
     is behind a dialog. Three effects each calling pause/resume raced, and the loser was
     whichever ran last. */
  useEffect(() => {
    if (!animator) return;
    if (wanted && visible && !zoomed) animator.resume();
    else animator.pause();
  }, [animator, wanted, visible, zoomed]);

  const ready = animator !== null;

  return (
    <>
      <figure
        data-slot="kith_mermaid_flow"
        className="group border-border/60 bg-card/40 relative my-3 overflow-hidden rounded-xl border"
      >
        {/* The still diagram, in the flow of the page and giving the figure its size, until the
            animation is ready to take over. The animator's container is mounted underneath it
            the whole time and deliberately not hidden with `display: none` — a container with no
            layout has no geometry, and geometry is what a route through a diagram is made of. */}
        {ready ? null : error ? <div className="pb-6">{still}</div> : still}
        {/* `[&>svg]:h-full!` is not a flourish. The animator strips the drawing's width and
            height and sizes it from its own injected `.ma-container svg { height: 100% }` — which
            loses to this app's global svg reset, and the drawing comes out at width 100% and its
            *natural aspect height*: a 480×250 diagram drawn 770 wide and 400 tall, three quarters
            of it below the fold of a box that reported the right height. */}
        <div
          ref={box}
          data-slot="kith_flow_stage"
          aria-hidden={!ready}
          className={cn(
            "[&>svg]:h-full! [&>svg]:w-full!",
            ready ? "relative" : "pointer-events-none absolute inset-0 opacity-0",
          )}
          style={{ height: ready ? (height ?? DEFAULT_HEIGHT) : undefined }}
        />

        {/* Why it is not animating. Not a red box in the middle of a reply — the diagram is
            right there and correct — but not silence either, or the same broken script comes
            back in the next reply and the one after that. */}
        {error && !ready ? (
          <p className="text-muted-foreground/60 absolute inset-x-0 bottom-0 flex items-center gap-1.5 truncate px-3 py-1.5 font-mono text-[11px]">
            <AlertTriangle className="size-3 shrink-0" />
            {error}
          </p>
        ) : null}

        {ready ? (
          <>
            <div className="absolute end-2 top-2 flex gap-1 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100">
              <CopyFrame code={code} animator={animator} dark={dark} />
              <OverlayButton label="Open the diagram full screen" onClick={() => setZoomed(true)}>
                <Maximize2 className="size-3.5" />
              </OverlayButton>
            </div>
            <Transport
              animator={animator}
              playing={wanted}
              onPlaying={setWanted}
              onScrubbed={() => setWanted(true)}
            />
            <ResizeHandle
              height={height ?? DEFAULT_HEIGHT}
              onChange={(next) => {
                setSized(true);
                setHeight(next);
              }}
            />
          </>
        ) : null}
      </figure>
      {zoomed ? (
        <Lightbox
          code={code}
          dark={dark}
          startAt={animator?.currentTime() ?? 0}
          onClose={() => setZoomed(false)}
        />
      ) : null}
    </>
  );
}

/**
 * Play, pause, and where in the story you are.
 *
 * Always on screen rather than revealed on hover, unlike the two buttons above it. A choreographed
 * diagram is a thing with a length — you cannot tell by looking at a still frame whether you are
 * three steps in or one, and a control that has to be discovered by hovering is a control that is
 * not there when the question arises.
 *
 * The playhead is written straight to the DOM from the animator's own frame callback. Sixty
 * `setState`s a second would re-render this subtree sixty times a second, per diagram, forever —
 * and it would be for nothing, because the only thing that changes is one width and one number.
 */
function Transport({
  animator,
  playing,
  onPlaying,
  onScrubbed,
  className,
}: {
  animator: MermaidAnimator;
  playing: boolean;
  onPlaying: (playing: boolean) => void;
  onScrubbed: () => void;
  /** Absolute over the drawing full screen, where the drawing is the whole window; in the flow
   *  of the figure inline, where a bar laid over it would cover the bottom of the diagram. */
  className?: string;
}) {
  const fill = useRef<HTMLDivElement>(null);
  const clock = useRef<HTMLSpanElement>(null);
  const track = useRef<HTMLDivElement>(null);
  const [duration, setDuration] = useState(0);
  const [marks, setMarks] = useState<SceneMarker[]>([]);
  /** True only for the length of a drag, so a scrub does not fight the playhead. */
  const scrubbing = useRef(false);

  useEffect(() => {
    setDuration(animator.duration());
    setMarks(animator.markers());
    let shown = -1;
    animator.onTick((at, total) => {
      const fraction = total > 0 ? at / total : 0;
      if (fill.current) fill.current.style.transform = `scaleX(${fraction})`;
      // The readout only says tenths, so it only has to be written ten times a second.
      const tenths = Math.floor(at / 100);
      if (clock.current && tenths !== shown) {
        shown = tenths;
        clock.current.textContent = `${(at / 1000).toFixed(1)}s`;
      }
    });
    return () => animator.onTick(null);
  }, [animator]);

  const seekTo = useCallback(
    (event: React.PointerEvent) => {
      const el = track.current;
      if (!el || duration <= 0) return;
      const rect = el.getBoundingClientRect();
      const fraction = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
      animator.seek(fraction * duration);
      if (fill.current) fill.current.style.transform = `scaleX(${fraction})`;
    },
    [animator, duration],
  );

  return (
    <div
      className={cn(
        "border-border/50 bg-card/60 flex items-center gap-2.5 border-t px-2 py-1 backdrop-blur-sm",
        className,
      )}
    >
      <button
        type="button"
        aria-label={playing ? "Pause the animation" : "Play the animation"}
        onClick={() => onPlaying(!playing)}
        className="text-muted-foreground hover:text-foreground hover:bg-muted/60 grid size-5 shrink-0 place-items-center rounded transition-colors"
      >
        {playing ? <Pause className="size-3" /> : <Play className="size-3" />}
      </button>

      {/* A row of steps, not a bare bar. `markers()` is the compiled timeline — every route,
          wait and state change in the script — and drawing them means the bar answers "how far
          through, out of how many things" rather than only "how far through". */}
      <div
        ref={track}
        role="slider"
        tabIndex={0}
        aria-label="Seek the animation"
        aria-valuemin={0}
        aria-valuemax={Math.round(duration)}
        aria-valuenow={0}
        className="relative h-4 grow cursor-pointer touch-none"
        onPointerDown={(event) => {
          scrubbing.current = true;
          animator.pause();
          event.currentTarget.setPointerCapture(event.pointerId);
          seekTo(event);
        }}
        onPointerMove={(event) => {
          if (scrubbing.current) seekTo(event);
        }}
        onPointerUp={() => {
          scrubbing.current = false;
          // Whatever the button said before the drag, a scrub is someone asking to watch it from
          // here — so it plays on, rather than sitting still on a frame they chose.
          onScrubbed();
        }}
      >
        <div className="bg-muted/70 absolute inset-x-0 top-1/2 h-[3px] -translate-y-1/2 overflow-hidden rounded-full">
          <div
            ref={fill}
            className="bg-kith/80 h-full w-full origin-left"
            style={{ transform: "scaleX(0)" }}
          />
        </div>
        {duration > 0
          ? marks.map((mark, index) => (
              <span
                key={`${mark.atMs}-${index}`}
                title={mark.label}
                className="bg-border absolute top-1/2 h-2 w-px -translate-y-1/2"
                style={{ left: `${(mark.atMs / duration) * 100}%` }}
              />
            ))
          : null}
      </div>

      <span className="text-muted-foreground/60 shrink-0 font-mono text-[10px] tabular-nums">
        <span ref={clock}>0.0s</span>
        <span className="text-muted-foreground/35"> / {(duration / 1000).toFixed(1)}s</span>
      </span>
    </div>
  );
}

/**
 * The frame you are looking at, as a PNG.
 *
 * The same promise the still diagram's copy button makes — what you paste is what you were
 * looking at — which for something moving has to mean *this moment*, not the first one. The
 * animator will render a still of the scene at any time, and from there it is the existing
 * `toPng` path, painted onto the page background for the same reason.
 *
 * A separate entry point, imported when the button is pressed: it carries a GIF encoder, and
 * nobody should download one for a diagram they only looked at.
 */
function CopyFrame({
  code,
  animator,
  dark,
}: {
  code: string;
  animator: MermaidAnimator;
  dark: boolean;
}) {
  /** Three-valued, deliberately. A copy that silently failed and showed a tick is a lie the
   *  person only discovers when they paste. */
  const [state, setState] = useState<"idle" | "done" | "failed">("idle");

  const copy = useCallback(async () => {
    const settle = (next: "done" | "failed") => {
      setState(next);
      setTimeout(() => setState("idle"), 2200);
    };
    try {
      const { exportSvg } = await import("mermaid-animator/export");
      const svg = await exportSvg(code, {
        at: animator.currentTime(),
        theme: animatorTheme(dark),
        mermaid: mermaidOptions(dark),
      });
      const png = await toPng(svg, PALETTE[dark ? "dark" : "light"].background);
      if (!png) return settle("failed");
      await navigator.clipboard.write([new ClipboardItem({ "image/png": png })]);
      settle("done");
    } catch {
      settle("failed");
    }
  }, [code, animator, dark]);

  return (
    <OverlayButton
      label={state === "failed" ? "Could not copy the diagram" : "Copy this frame as an image"}
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
  );
}

/**
 * The bottom edge, draggable.
 *
 * The measured height is right for most diagrams and wrong for the two ends: a wide flat one
 * leaves space above and below, a tall one hits the ceiling and is drawn small. Same handle,
 * same bounds and same reasoning as the canvas — see `html-canvas.tsx`.
 */
function ResizeHandle({
  height,
  onChange,
}: {
  height: number;
  onChange: (height: number) => void;
}) {
  const from = useRef<{ y: number; height: number } | null>(null);

  return (
    <div
      role="separator"
      aria-label="Drag to resize the diagram"
      aria-orientation="horizontal"
      className="hover:after:bg-border absolute inset-x-0 -bottom-px h-1.5 cursor-ns-resize touch-none after:absolute after:inset-x-0 after:bottom-0 after:h-px after:bg-transparent"
      onPointerDown={(event) => {
        from.current = { y: event.clientY, height };
        event.currentTarget.setPointerCapture(event.pointerId);
      }}
      onPointerMove={(event) => {
        const start = from.current;
        if (!start) return;
        onChange(boxed(start.height + (event.clientY - start.y)));
      }}
      onPointerUp={() => {
        from.current = null;
      }}
    />
  );
}

/**
 * The whole window, and here you can move around in it.
 *
 * A second animator rather than the inline one moved: reparenting an SVG mid-animation means
 * re-rendering it anyway, and this way the inline diagram is still where you left it when you
 * close. It opens at the time the inline one had reached, because a full-screen view that starts
 * the story over is a full-screen view you have to wait for.
 *
 * Pan and zoom are on here and off inline, and that is the point of coming here. Through a
 * portal to `<body>` for the reason the still diagram's lightbox documents at length: the
 * message this sits inside carries `content-visibility: auto`, which makes it the containing
 * block for `position: fixed`, so "full screen" otherwise comes out the size of the message.
 */
function Lightbox({
  code,
  dark,
  startAt,
  onClose,
}: {
  code: string;
  dark: boolean;
  startAt: number;
  onClose: () => void;
}) {
  const [playing, setPlaying] = useState(true);
  const { box, animator } = useAnimator({
    code,
    dark,
    explorable: true,
    enabled: true,
    // Nothing to settle: this only opens on a diagram that has already been built once.
    delay: 0,
    startAt,
  });

  useEffect(() => {
    if (!animator) return;
    if (playing) animator.resume();
    else animator.pause();
  }, [animator, playing]);

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
      aria-label="Diagram"
      className="bg-background fixed inset-0 z-50 overflow-hidden"
    >
      <div ref={box} className="size-full" />
      {animator ? (
        <Transport
          className="absolute inset-x-0 bottom-0"
          animator={animator}
          playing={playing}
          onPlaying={setPlaying}
          onScrubbed={() => setPlaying(true)}
        />
      ) : null}
      <div className="absolute end-4 top-4 flex items-center gap-1">
        <OverlayButton label="Close" onClick={onClose}>
          <X className="size-3.5" />
        </OverlayButton>
      </div>
    </div>,
    document.body,
  );
}
