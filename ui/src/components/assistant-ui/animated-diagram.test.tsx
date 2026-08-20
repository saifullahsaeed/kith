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
import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AnimatedDiagram } from "@/components/assistant-ui/animated-diagram";
import { PALETTE } from "@/lib/kith-palette";

const CODE = `---
flow:
  loop:
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
  /** What the validator says about the script, per test. */
  validation: { ok: true, checked: "graph" } as Record<string, unknown>,
  /** What mermaid is pretended to have laid out, which is what the box measures itself from. */
  viewBox: "0 0 400 260",
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
    onTick() {}
  }
  return {
    MermaidAnimator: FakeAnimator,
    validateFlowInDiagram: () => Promise.resolve(fake.validation),
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
  fake.viewBox = "0 0 400 260";
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
    expect(screen.queryByTestId("still")).not.toBeInTheDocument();
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

  it("has play, pause and a timeline once it is running", async () => {
    render(<AnimatedDiagram code={CODE} still={still} />);
    await settle();
    expect(screen.getByLabelText("Pause the animation")).toBeInTheDocument();
    expect(screen.getByLabelText("Seek the animation")).toBeInTheDocument();
    await act(async () => {
      screen.getByLabelText("Pause the animation").click();
    });
    expect(screen.getByLabelText("Play the animation")).toBeInTheDocument();
    expect(fake.paused).toBeGreaterThan(0);
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

  it("takes the animation down with the message", async () => {
    const view = render(<AnimatedDiagram code={CODE} still={still} />);
    await settle();
    expect(fake.destroyed).toBe(0);
    view.unmount();
    expect(fake.destroyed).toBe(1);
  });
});
