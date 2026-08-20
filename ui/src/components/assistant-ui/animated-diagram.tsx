import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, Check, Copy, Maximize2, Pause, Play, RotateCw, X } from "lucide-react";

import type { FlowValidation, MermaidAnimator, SceneMarker, Theme } from "mermaid-animator";

import { OverlayButton } from "@/components/assistant-ui/overlay-button";
import { animatorTheme, defaultFlowColour } from "@/lib/animator-theme";
import { toPng } from "@/lib/diagram";
import { useFlowFailures } from "@/lib/flow-failures";
import { loopsForever } from "@/lib/flow-script";
import { PALETTE } from "@/lib/kith-palette";
import { mermaidConfig, mermaidEngine, mermaidOptions, sweepOrphans } from "@/lib/mermaid-config";
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
 * And the line goes to him as well as to them. He cannot see this screen, so without that the
 * same broken route comes back in the next reply and the one after it — the person is the only
 * one who ever knew, and fixing it means typing the error out by hand. It rides along with
 * whatever they say next; see `lib/flow-failures.ts`.
 *
 * **Inline, it does not pan and does not zoom.** The animator's wheel handler calls
 * `preventDefault` unconditionally, so leaving zoom on would mean the conversation stops
 * scrolling whenever the pointer crosses a diagram. Full screen is where you go to look around,
 * which is the same division the still diagram already has.
 *
 * **It plays once, and then it is a diagram again.** The library only knows how to go round and
 * round, so this watches for the wrap and stops. A fourth pass explains nothing; it is just
 * something moving beside the paragraph someone is trying to read. `loop:` instead of `steps:`
 * in the script is him asking for the repeat, and then it repeats — see
 * `lib/flow-script.loopsForever`.
 *
 * What is on screen once it has finished is the *still* diagram, swapped back in. Every frame of
 * a flow script has a focus set, including the first and the last, so every frame dims most of
 * the picture — there is no moment in the animation that looks like the ordinary drawing, and
 * leaving it parked on one means leaving three quarters of the diagram greyed out under a
 * finished explanation. The still renderer is already here and already correct, so the end of
 * the animation is the still diagram and the replay button swaps back. Both are kept mounted:
 * re-rendering mermaid at the end of every animation would flash "drawing…" at someone who was
 * looking at a finished picture.
 *
 * **It does not start until it has been seen, and it stops when it is not being.** A reply is
 * scrolled to, so a diagram five screens down would otherwise play to the end and stop before
 * anyone reached it — and ten of them would be ten animation loops against a renderer that has
 * been frozen by less. Nothing runs until the box is on screen, and it pauses when it leaves.
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
function options(theme: Theme, dark: boolean, explorable: boolean) {
  return {
    // Handed in rather than built here, because by this point it may carry a name he invented
    // that the validator agreed to — and the renderer validates the script again against the
    // theme it is given. Rebuilding it here dropped that agreement on the floor: the script
    // passed the check and then failed the render, for the same reason it had passed.
    theme,
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
  /** What was drawn differently from what he asked for, when the asking was a word nobody
   *  defines. Not an error: the animation is running. */
  const [note, setNote] = useState<string | null>(null);
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
          /* Parsed before the library is allowed anywhere near it, which is the same guard the
             still renderer has always had and the reason it never showed this: `mermaid.render`
             on a diagram it cannot draw leaves its own cartoon bomb in the body, and the animator
             renders inside the library, where there is no parse step to add one to. An
             unparseable fence is not an animation that failed — it is a code block, which is
             exactly what the still renderer beside this will make of it. */
          const mermaid = await mermaidEngine();
          mermaid.initialize(mermaidConfig(dark));
          if (!(await mermaid.parse(code, { suppressErrors: true }))) return;
          if (cancelled) return;
          const theme = animatorTheme(dark);
          // Checked before anything is drawn, and against the diagram rather than only against
          // the schema: the two mistakes worth catching — a route naming a node that is not
          // there, and a hop with no edge under it — are both invisible to a validator that has
          // not read the graph. Told about this theme's own colour and state names, or every
          // name Kith adds to the palette would come back as an unknown one.
          const { check, invented } = await agreeOnNames(theme, code, dark, validateFlowInDiagram);
          if (cancelled) return;
          if (!check.ok) {
            setError(check.line ? `${check.message} (line ${check.line})` : check.message);
            return;
          }
          setNote(invented.length ? invented.join("; ") : null);
          built = await MermaidAnimator.create(container, code, options(theme, dark, explorable));
          // The library renders without a container of its own — it cannot be given one — so
          // whatever it left at the top of the page goes now that its promise has settled. Both
          // ways round: a render can leave something behind and still succeed.
          sweepOrphans();
          if (cancelled) {
            built.destroy();
            return;
          }
          // `create` starts it. Stopped again immediately, before anything is on screen, so that
          // one effect below is the only thing that ever decides whether this is running —
          // otherwise every diagram plays a frame or two of itself on the way to being paused.
          built.pause();
          if (resumeAt.current) built.seek(resumeAt.current);
          setError(null);
          setAnimator(built);
        } catch (thrown) {
          // A diagram that parsed and then would not lay out, which is the one way the bomb can
          // still get in. See `sweepOrphans`.
          sweepOrphans();
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

  return { box, animator, error, note };
}

/**
 * Come to terms over the names in his script.
 *
 * A colour or a state the theme does not define is a hard refusal, and it takes the whole
 * animation with it — `color: orange` cost a finished diagram its choreography and left a page
 * with an apology under it. Which is out of all proportion: the routes were right, the states
 * were right, the *timing* was right, and one word for a colour was not in a vocabulary he was
 * never shown.
 *
 * So a name nobody has heard of is taken as a name for the default colour, or for the working
 * state, and the diagram plays. The library reports the offending token in `value`, which is
 * what makes this exact rather than a guess — no parsing of his YAML here, and no rewriting of
 * what he wrote. Only the first bad name is reported per pass, so this goes round until the
 * script is either accepted or failing for a reason that is not a name.
 *
 * Bounded, because a script whose every step names a new colour is not a script to be patient
 * with, and because a loop that fixes and re-checks needs a reason it must stop.
 */
const FORGIVE_AT_MOST = 8;

async function agreeOnNames(
  theme: Theme,
  code: string,
  dark: boolean,
  validate: (code: string, vocabulary: { colors: string[]; states: string[] }) => Promise<FlowValidation>,
): Promise<{ check: FlowValidation; invented: string[] }> {
  const invented: string[] = [];
  const vocabulary = () => ({
    colors: Object.keys(theme.flowColors ?? {}),
    states: Object.keys(theme.states ?? {}),
  });
  let check = await validate(code, vocabulary());
  while (!check.ok && check.value && invented.length < FORGIVE_AT_MOST) {
    if (check.code === "UNKNOWN_COLOR" && theme.flowColors) {
      theme.flowColors[check.value] = defaultFlowColour(dark);
      invented.push(`unknown colour "${check.value}" — drawn in amber`);
    } else if (check.code === "UNKNOWN_STATE" && theme.states) {
      theme.states[check.value] = theme.states.busy;
      invented.push(`unknown state "${check.value}" — drawn as busy`);
    } else {
      break;
    }
    check = await validate(code, vocabulary());
  }
  return { check, invented };
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
  /** Whether it should be running, which starts as no: see the observer below. */
  const [wanted, setWanted] = useState(false);
  const [visible, setVisible] = useState(false);
  /** Played to the end and holding its last frame. Held here rather than in the bar because the
   *  full-screen view has to know too — opening it on the end frame, which then instantly ends
   *  again, is not opening it. */
  const [ended, setEnded] = useState(false);
  /** Whether he asked for the repeat. */
  const looping = useMemo(() => loopsForever(code), [code]);

  const { box, animator, error, note } = useAnimator({
    code,
    dark,
    explorable: false,
    enabled: !streaming,
    delay: SETTLE_MS,
  });

  /* Tell him what his screen cannot. Keyed per diagram so a reply with two broken ones says both,
     and dropped the moment this one leaves the screen or starts working — a diagram he has since
     fixed is not something to bring up. */
  const id = useId();
  const reportFailure = useFlowFailures((state) => state.report);
  const forgetFailure = useFlowFailures((state) => state.forget);
  useEffect(() => {
    if (error) reportFailure(id, error);
    else forgetFailure(id);
    return () => forgetFailure(id);
  }, [error, id, reportFailure, forgetFailure]);

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
  /** Whether it has ever been on screen. First sight is what starts it; a later one is not,
   *  because scrolling back to something you deliberately stopped and having it start again is
   *  the control not working. */
  const seen = useRef(false);

  useEffect(() => {
    const el = box.current;
    if (!el || typeof IntersectionObserver === "undefined") {
      // Nothing can be said about visibility, so the honest fallback is the old behaviour.
      seen.current = true;
      setVisible(true);
      setWanted(true);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        setVisible(entry.isIntersecting);
        if (entry.isIntersecting && !seen.current) {
          seen.current = true;
          setWanted(true);
        }
      },
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
  /** Which of the two drawings is the one on screen. The animation while it is playing or
   *  waiting to be played; the still diagram before it exists and after it has finished. */
  const showing = ready && !ended ? "animation" : "still";

  return (
    <>
      <figure
        data-slot="kith_mermaid_flow"
        className="group border-border/60 bg-card/40 relative my-3 overflow-hidden rounded-xl border"
      >
        {/* The still diagram: on screen before the animation is ready, and again once it has
            finished, and mounted the whole way through either way. Not unmounted while the
            animation plays, because putting it back would mean rendering mermaid a second time
            and flashing a placeholder at the end of every animation.

            The animator's container is mounted underneath it from the start and deliberately not
            hidden with `display: none` while it is being built — a container with no layout has
            no geometry, and geometry is what a route through a diagram is made of. */}
        <div hidden={showing === "animation"} className={cn(error && !ready && "pb-6")}>
          {still}
        </div>
        {/* `[&>svg]:h-full!` is not a flourish. The animator strips the drawing's width and
            height and sizes it from its own injected `.ma-container svg { height: 100% }` — which
            loses to this app's global svg reset, and the drawing comes out at width 100% and its
            *natural aspect height*: a 480×250 diagram drawn 770 wide and 400 tall, three quarters
            of it below the fold of a box that reported the right height. */}
        <div
          ref={box}
          data-slot="kith_flow_stage"
          aria-hidden={showing !== "animation"}
          hidden={ready && showing !== "animation"}
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
            {/* A word that was drawn differently from the way he asked for it. Under the
                drawing rather than in place of it, because the diagram is running: this is the
                only thing on screen that says the colour you are looking at is not the colour
                in the source. */}
            {note ? (
              <p className="text-muted-foreground/45 border-border/40 flex items-center gap-1.5 truncate border-t px-3 py-1 font-mono text-[10px]">
                <AlertTriangle className="size-2.5 shrink-0" />
                {note}
              </p>
            ) : null}
            <Transport
              animator={animator}
              playing={wanted}
              ended={ended}
              looping={looping}
              onPlaying={setWanted}
              onEnded={setEnded}
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
          looping={looping}
          // Where the inline one had got to — unless that is the last frame, which as a starting
          // point is a full-screen view that opens finished.
          startAt={ended ? 0 : (animator?.currentTime() ?? 0)}
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
  ended,
  looping,
  onPlaying,
  onEnded,
  className,
}: {
  animator: MermaidAnimator;
  playing: boolean;
  /** Played to the end and stopped there. Owned by the caller; reported from here. */
  ended: boolean;
  looping: boolean;
  onPlaying: (playing: boolean) => void;
  onEnded: (ended: boolean) => void;
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
  /** Where the clock was on the previous frame, which is how the wrap is spotted. A ref rather
   *  than a variable inside the effect below, because every deliberate jump *backwards* — a
   *  replay, a scrub to an earlier point — looks exactly like a wrap from the next frame's point
   *  of view, and has to be able to say so. Both did, and both ended the animation on the spot:
   *  replay stopped again a frame after starting, and scrubbing back finished a diagram you were
   *  in the middle of. */
  const previous = useRef(0);

  useEffect(() => {
    setDuration(animator.duration());
    setMarks(animator.markers());
    let shown = -1;
    animator.onTick((at, total) => {
      /* The end of a play-once animation, which is the only end there is to find: the library's
         clock is `elapsed % duration`, so it has no notion of finishing — a cycle completing
         shows up here as the time having gone backwards. Caught within a frame of the wrap,
         which is 16ms of the second pass nobody sees. */
      let now = at;
      if (!looping && total > 0 && at < previous.current) {
        animator.pause();
        // Not `seek(total)`: that is `total % total`, which is the beginning. A millisecond
        // short of the end is the last frame, and holding it is the point.
        animator.seek(total - 1);
        onEnded(true);
        onPlaying(false);
        now = total;
      }
      previous.current = at;
      const fraction = total > 0 ? now / total : 0;
      if (fill.current) fill.current.style.transform = `scaleX(${fraction})`;
      // The readout only says tenths, so it only has to be written ten times a second.
      const tenths = Math.floor(now / 100);
      if (clock.current && tenths !== shown) {
        shown = tenths;
        clock.current.textContent = `${(now / 1000).toFixed(1)}s`;
      }
    });
    return () => animator.onTick(null);
  }, [animator, looping, onEnded, onPlaying]);

  const seekTo = useCallback(
    (event: React.PointerEvent) => {
      const el = track.current;
      if (!el || duration <= 0) return;
      const rect = el.getBoundingClientRect();
      const fraction = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
      const at = fraction * duration;
      animator.seek(at);
      previous.current = at;
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
      {/* Stop, start, or run it again — and the third is a state of its own rather than the
          second one over again, because pressing play on an animation stopped one millisecond
          from its end would play that millisecond, wrap, and stop right back where it was. */}
      <button
        type="button"
        aria-label={
          ended
            ? "Play the animation again"
            : playing
              ? "Stop the animation"
              : "Play the animation"
        }
        onClick={() => {
          if (ended) {
            animator.seek(0);
            previous.current = 0;
            onEnded(false);
            onPlaying(true);
            return;
          }
          onPlaying(!playing);
        }}
        className="text-muted-foreground hover:text-foreground hover:bg-muted/60 grid size-5 shrink-0 place-items-center rounded transition-colors"
      >
        {ended ? (
          <RotateCw className="size-3" />
        ) : playing ? (
          <Pause className="size-3" />
        ) : (
          <Play className="size-3" />
        )}
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
          // here — so it plays on, rather than sitting still on a frame they chose. And it is no
          // longer finished, wherever it had stopped.
          //
          // Resumed here as well as declared, and that is not belt-and-braces. The effect that
          // owns playback only runs when its state changes, so a drag begun while it was already
          // playing declares nothing new — and the `pause` that started the drag was never
          // lifted. It stopped dead on the frame you let go of, with a bar that said it was
          // running.
          animator.resume();
          onEnded(false);
          onPlaying(true);
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
  looping,
  startAt,
  onClose,
}: {
  code: string;
  dark: boolean;
  looping: boolean;
  startAt: number;
  onClose: () => void;
}) {
  const [playing, setPlaying] = useState(true);
  const [ended, setEnded] = useState(false);
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
          ended={ended}
          looping={looping}
          onPlaying={setPlaying}
          onEnded={setEnded}
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
