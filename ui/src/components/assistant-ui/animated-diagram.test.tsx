/**
 * What the animated fence does around the animation.
 *
 * The animation itself cannot be tested here — routing a packet through a diagram is real SVG
 * geometry, and jsdom has none — so the library is stood in for and what is checked is every
 * decision this component makes on its own: when it is allowed to build, what it builds with,
 * what is on screen while it cannot, and what happens to a flow script that will not compile.
 *
 * Each of these is a thing that would look like the feature being broken and be a scheduling or
 * a fallback bug: a diagram that never animates because it was built from half a fence, a
 * conversation that will not scroll because the drawing ate the wheel, a typo in a route
 * throwing away a perfectly good picture.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AnimatedDiagram } from "@/components/assistant-ui/animated-diagram";
import { currentFlowFailures, useFlowFailures } from "@/lib/flow-failures";
import { PALETTE } from "@/lib/kith-palette";

const CODE = `---
flow:
  steps:
    - route: [A, B]
---
flowchart LR
  A[Client] --> B[Server]`;

type Built = { container: HTMLElement; code: string; options: Record<string, unknown> };

const fake = vi.hoisted(() => ({
  built: [] as Built[],
  destroyed: 0,
  resumed: 0,
  paused: 0,
  seeked: [] as number[],
  /** What the validator says about the script. A function, because forgiving a name means
   *  asking again with a wider vocabulary and getting a different answer. */
  validate: null as
    | ((code: string, vocabulary: { colors: string[]; states: string[] }) => Record<string, unknown>)
    | null,
  /** The simple case: one fixed answer. */
  validation: { ok: true, checked: "graph" } as Record<string, unknown>,
  /** What mermaid is pretended to have laid out, which is what the box measures itself from. */
  viewBox: "0 0 400 260",
  /** The bar's frame callback, so a test can be the clock. */
  tick: null as ((at: number, total: number) => void) | null,
  /** Whether mermaid can draw the diagram at all. */
  parses: true,
}));

/* Mermaid itself is stood in for. The component parses the fence before the animator is allowed
   to see it — a diagram that reaches `mermaid.render` unparsed leaves a cartoon bomb in the body
   — and loading a megabyte of real parser to answer "yes" is a slow way to test nothing. */
vi.mock("mermaid", () => ({
  default: {
    initialize: () => {},
    parse: () => Promise.resolve(fake.parses),
  },
}));

vi.mock("mermaid-animator", () => {
  class FakeAnimator {
    static async create(container: HTMLElement, code: string, options: Record<string, unknown>) {
      fake.built.push({ container, code, options });
      container.innerHTML = `<svg viewBox="${fake.viewBox}"></svg>`;
      return new FakeAnimator();
    }
    destroy() {
      fake.destroyed += 1;
    }
    pause() {
      fake.paused += 1;
    }
    resume() {
      fake.resumed += 1;
    }
    seek(at: number) {
      fake.seeked.push(at);
    }
    currentTime() {
      return 0;
    }
    duration() {
      return 3000;
    }
    markers() {
      return [{ atMs: 0, kind: "route" as const, label: "A → B" }];
    }
    onTick(listener: ((at: number, total: number) => void) | null) {
      fake.tick = listener;
    }
  }
  return {
    MermaidAnimator: FakeAnimator,
    validateFlowInDiagram: (code: string, vocabulary: { colors: string[]; states: string[] }) =>
      Promise.resolve(fake.validate ? fake.validate(code, vocabulary) : fake.validation),
  };
});

const still = <div data-testid="still">the diagram, holding still</div>;

/** Past the settle window, and past the dynamic import, the validation and the render that sit
 *  between it and something on screen — advancing timers alone leaves those promises pending. */
async function settle(ms = 300) {
  await act(async () => {
    vi.advanceTimersByTime(ms);
    for (let i = 0; i < 8; i++) await Promise.resolve();
  });
}

function box() {
  return document.querySelector<HTMLElement>('[data-slot="kith_mermaid_flow"]');
}

/** A drag on the timeline, from grab to let go, at a fraction along it. jsdom gives every
 *  element a zero-sized box, so the fraction is fed through `getBoundingClientRect`. */
function scrub(track: HTMLElement, fraction: number) {
  // jsdom has no pointer capture at all, and the handler calls it on the way in.
  track.setPointerCapture = () => {};
  track.releasePointerCapture = () => {};
  track.getBoundingClientRect = () =>
    ({ left: 0, width: 100, top: 0, height: 16, right: 100, bottom: 16, x: 0, y: 0 }) as DOMRect;
  fireEvent.pointerDown(track, { clientX: fraction * 100, pointerId: 1 });
  fireEvent.pointerUp(track, { clientX: fraction * 100, pointerId: 1 });
}

/** The animator's own container: the only div in the figure that it was handed. */
function stage() {
  return fake.built.at(-1)?.container;
}

beforeEach(() => {
  vi.useFakeTimers();
  fake.built = [];
  fake.destroyed = 0;
  fake.resumed = 0;
  fake.paused = 0;
  fake.seeked = [];
  fake.validation = { ok: true, checked: "graph" };
  fake.validate = null;
  fake.parses = true;
  fake.viewBox = "0 0 400 260";
  fake.tick = null;
  useFlowFailures.getState().clear();
});

describe("an animated mermaid fence", () => {
  it("shows the still diagram until the animation is up", () => {
    render(<AnimatedDiagram code={CODE} still={still} />);
    expect(screen.getByTestId("still")).toBeInTheDocument();
    expect(fake.built).toHaveLength(0);
  });

  it("does not build one from a fence he is still writing", async () => {
    render(<AnimatedDiagram code={CODE} still={still} streaming />);
    await settle(2000);
    expect(fake.built).toHaveLength(0);
    expect(screen.getByTestId("still")).toBeInTheDocument();
  });

  it("builds it once he has stopped, and takes the still one down", async () => {
    const view = render(<AnimatedDiagram code={CODE} still={still} streaming />);
    await settle();
    expect(fake.built).toHaveLength(0);
    // The reply ends. Nothing else about the fence changes.
    view.rerender(<AnimatedDiagram code={CODE} still={still} />);
    await settle();
    expect(fake.built).toHaveLength(1);
    expect(fake.built[0].code).toBe(CODE);
    // Kept mounted and hidden rather than unmounted: it comes back when the animation ends, and
    // rendering mermaid a second time to put it there would flash a placeholder.
    expect(screen.getByTestId("still")).not.toBeVisible();
  });

  it("gives the animator a container that is laid out, not hidden", async () => {
    // Geometry is what a route through a diagram is made of, and `display: none` has none.
    render(<AnimatedDiagram code={CODE} still={still} />);
    const before = box()?.querySelector('[data-slot="kith_flow_stage"]');
    expect(before?.className).not.toContain("hidden");
    expect(before?.className).toContain("absolute");
    await settle();
    expect(stage()?.className).not.toContain("opacity-0");
  });

  it("does not take the wheel or the pointer inline", async () => {
    // The animator's wheel handler calls preventDefault unconditionally, so zoom on an inline
    // diagram means the conversation stops scrolling wherever one is.
    render(<AnimatedDiagram code={CODE} still={still} />);
    await settle();
    expect(fake.built[0].options).toMatchObject({ pan: false, zoom: false, inspect: true });
  });

  it("draws it with the same mermaid the still one uses", async () => {
    // One config for both renderers, because there is one mermaid — see `lib/mermaid-config.ts`.
    render(<AnimatedDiagram code={CODE} still={still} />);
    await settle();
    const config = fake.built[0].options.mermaid as Record<string, Record<string, string>>;
    expect(config.themeVariables.lineColor).toBe(PALETTE.light.dim);
    expect(config.htmlLabels).toBe(false);
    expect((fake.built[0].options.theme as { name: string }).name).toBe("kith");
  });

  it("is as tall as mermaid laid the diagram out, so nothing is magnified", async () => {
    render(<AnimatedDiagram code={CODE} still={still} />);
    await settle();
    expect(stage()?.style.height).toBe("260px");
  });

  it("shrinks a diagram taller than the box may be, rather than cropping it", async () => {
    fake.viewBox = "0 0 400 4000";
    render(<AnimatedDiagram code={CODE} still={still} />);
    await settle();
    expect(stage()?.style.height).toBe("1200px");
  });

  it("has stop, play and a timeline once it is running", async () => {
    render(<AnimatedDiagram code={CODE} still={still} />);
    await settle();
    expect(screen.getByLabelText("Stop the animation")).toBeInTheDocument();
    expect(screen.getByLabelText("Seek the animation")).toBeInTheDocument();
    await act(async () => {
      screen.getByLabelText("Stop the animation").click();
    });
    expect(screen.getByLabelText("Play the animation")).toBeInTheDocument();
    expect(fake.paused).toBeGreaterThan(0);
  });

  it("plays once and stops on its last frame", async () => {
    // The library's clock is `elapsed % duration`, so the only sign that a cycle finished is the
    // time going backwards. Nothing else can be watched for.
    render(<AnimatedDiagram code={CODE} still={still} />);
    await settle();
    await act(async () => {
      fake.tick?.(2400, 3000);
      fake.tick?.(12, 3000);
    });
    // Held a millisecond short of the end, because seeking to the duration is modulo the
    // duration, which is the beginning.
    expect(fake.seeked).toContain(2999);
    expect(screen.getByLabelText("Play the animation again")).toBeInTheDocument();
  });

  it("runs it again from the start, not from where it stopped", async () => {
    render(<AnimatedDiagram code={CODE} still={still} />);
    await settle();
    await act(async () => {
      fake.tick?.(2400, 3000);
      fake.tick?.(12, 3000);
    });
    await act(async () => {
      screen.getByLabelText("Play the animation again").click();
    });
    expect(fake.seeked.at(-1)).toBe(0);
    expect(screen.getByLabelText("Stop the animation")).toBeInTheDocument();
  });

  it("keeps going round when he asked for a loop", async () => {
    const looped = CODE.replace("  steps:", "  loop:");
    render(<AnimatedDiagram code={looped} still={still} />);
    await settle();
    await act(async () => {
      fake.tick?.(2400, 3000);
      fake.tick?.(12, 3000);
    });
    expect(fake.seeked).not.toContain(2999);
    expect(screen.getByLabelText("Stop the animation")).toBeInTheDocument();
  });

  it("does not mistake a scrub backwards for the end", async () => {
    // Every deliberate jump back looks like a wrap from the next frame's point of view.
    render(<AnimatedDiagram code={CODE} still={still} />);
    await settle();
    const track = screen.getByLabelText("Seek the animation");
    await act(async () => {
      fake.tick?.(1800, 3000);
      scrub(track, 0.2);
      fake.tick?.(600, 3000);
    });
    expect(screen.getByLabelText("Stop the animation")).toBeInTheDocument();
    expect(fake.seeked).not.toContain(2999);
  });

  it("is still running after a scrub that started while it was running", async () => {
    // The pause that begins a drag has to be lifted by the drag, because the effect that owns
    // playback hears nothing new when the state it watches has not changed.
    render(<AnimatedDiagram code={CODE} still={still} />);
    await settle();
    const track = screen.getByLabelText("Seek the animation");
    const before = fake.resumed;
    await act(async () => {
      scrub(track, 0.5);
    });
    expect(fake.resumed).toBeGreaterThan(before);
  });

  it("draws a colour nobody defines rather than refusing the diagram", async () => {
    // What actually happened: `color: orange`, and a finished diagram lost its whole animation
    // to one word. The library names the offending token, so this is exact.
    let asked = 0;
    fake.validate = (_code, vocabulary) => {
      asked += 1;
      if (vocabulary.colors.includes("turquoise")) return { ok: true, checked: "graph" };
      return { ok: false, code: "UNKNOWN_COLOR", message: "unknown color", value: "turquoise" };
    };
    render(<AnimatedDiagram code={CODE} still={still} />);
    await settle();
    expect(asked).toBe(2);
    expect(fake.built).toHaveLength(1);
    const theme = fake.built[0].options.theme as { flowColors: Record<string, string> };
    expect(theme.flowColors.turquoise).toBeTruthy();
    expect(screen.getByText(/unknown colour "turquoise" — drawn in amber/)).toBeInTheDocument();
    // Running, not apologising.
    expect(screen.getByLabelText("Stop the animation")).toBeInTheDocument();
    expect(screen.getByTestId("still")).not.toBeVisible();
  });

  it("gives up on a script that is wrong for a reason that is not a name", async () => {
    fake.validate = () => ({
      ok: false,
      code: "NO_EDGE",
      message: 'flow: no edge between "A" and "C"',
    });
    render(<AnimatedDiagram code={CODE} still={still} />);
    await settle();
    expect(fake.built).toHaveLength(0);
    expect(screen.getByText(/no edge between/)).toBeInTheDocument();
  });

  it("never lets a diagram mermaid cannot draw reach the animator", async () => {
    // The library renders inside itself, with no parse step to add one to — and `mermaid.render`
    // on a diagram it cannot draw builds its own error graphic in a scratch element parented to
    // the body, then throws and leaves it there. It is a cartoon bomb the size of a paragraph,
    // it lands under the composer attached to nothing, and it was two of them for two diagrams.
    fake.parses = false;
    render(<AnimatedDiagram code={CODE} still={still} />);
    await settle();
    expect(fake.built).toHaveLength(0);
    // The still renderer owns this case, exactly as it did before any of this existed.
    expect(screen.getByTestId("still")).toBeVisible();
    expect(screen.queryByLabelText("Seek the animation")).not.toBeInTheDocument();
  });

  it("keeps the diagram and says why when the choreography will not compile", async () => {
    fake.validation = {
      ok: false,
      code: "UNKNOWN_NODE",
      message: 'flow: unknown node "Nowhere" in a route',
    };
    render(<AnimatedDiagram code={CODE} still={still} />);
    await settle();
    expect(fake.built).toHaveLength(0);
    expect(screen.getByTestId("still")).toBeInTheDocument();
    expect(screen.getByText(/unknown node "Nowhere"/)).toBeInTheDocument();
    // Not a play button on something that is not playing.
    expect(screen.queryByLabelText("Pause the animation")).not.toBeInTheDocument();
  });

  it("says which line, when the validator knows", async () => {
    fake.validation = {
      ok: false,
      code: "YAML_SYNTAX",
      message: "Tabs are not allowed for indentation",
      line: 3,
    };
    render(<AnimatedDiagram code={CODE} still={still} />);
    await settle();
    expect(screen.getByText(/Tabs are not allowed for indentation \(line 3\)/)).toBeInTheDocument();
  });

  it("keeps the diagram when the render itself throws", async () => {
    fake.validation = { ok: true, checked: "schema" };
    const library = await import("mermaid-animator");
    vi.spyOn(library.MermaidAnimator, "create").mockRejectedValue(
      new Error("flow: no edge between \"A\" and \"C\""),
    );
    render(<AnimatedDiagram code={CODE} still={still} />);
    await settle();
    expect(screen.getByTestId("still")).toBeInTheDocument();
    expect(screen.getByText(/no edge between/)).toBeInTheDocument();
  });

  it("tells him the choreography was refused, since he cannot see this screen", async () => {
    // Without this the same broken route comes back next reply: the person is the only one who
    // ever saw the error, and fixing it means typing it out by hand.
    fake.validation = {
      ok: false,
      code: "NO_EDGE",
      message: 'flow: no edge between "CompA" and "Modules"',
    };
    const view = render(<AnimatedDiagram code={CODE} still={still} />);
    await settle();
    expect(currentFlowFailures()).toEqual(['flow: no edge between "CompA" and "Modules"']);
    // And drops it the moment the diagram is off the screen — a reply nobody is looking at any
    // more is not context for the next thing they say.
    view.unmount();
    expect(currentFlowFailures()).toEqual([]);
  });

  it("says nothing about a diagram that worked", async () => {
    render(<AnimatedDiagram code={CODE} still={still} />);
    await settle();
    expect(currentFlowFailures()).toEqual([]);
  });

  it("takes the animation down with the message", async () => {
    const view = render(<AnimatedDiagram code={CODE} still={still} />);
    await settle();
    expect(fake.destroyed).toBe(0);
    view.unmount();
    expect(fake.destroyed).toBe(1);
  });
});
