/**
 * Clearing up after a render that went wrong.
 *
 * `mermaid.render` builds its diagram in a scratch element parented to `<body>`, and on failure
 * it draws its own error graphic *into* that element and rethrows — leaving it behind. The
 * graphic is a cartoon bomb reading "Syntax error in text", and because it hangs off the body
 * rather than off the conversation it appears at the bottom of the window, under the composer,
 * attached to nothing. Reported as "what the fuck is this", which is the correct reaction.
 */
import { beforeEach, describe, expect, it } from "vitest";

import { mermaidConfig, renderTarget, sweepOrphans } from "@/lib/mermaid-config";

function leftBehind(id: string) {
  const orphan = document.createElement("div");
  orphan.id = id;
  orphan.innerHTML = '<svg><g class="error-icon"></g></svg>';
  document.body.append(orphan);
  return orphan;
}

beforeEach(() => {
  document.body.innerHTML = "";
});

describe("what mermaid left in the body", () => {
  it("goes, whichever renderer left it", () => {
    // `d` plus the id the renderer passed: `kith-diagram-N` for the still one, `ma-…` for the
    // animator's own.
    leftBehind("dkith-diagram-7");
    leftBehind("dma-1787258162617-0");
    sweepOrphans();
    expect(document.body.children).toHaveLength(0);
  });

  it("goes even when there are several", () => {
    leftBehind("dma-1");
    leftBehind("dma-2");
    leftBehind("dma-3");
    sweepOrphans();
    expect(document.body.querySelectorAll(".error-icon")).toHaveLength(0);
  });

  it("takes nothing else with it", () => {
    const app = document.createElement("div");
    app.id = "root";
    const portal = document.createElement("div");
    portal.id = "dialog";
    document.body.append(app, portal);
    leftBehind("dma-1");
    sweepOrphans();
    expect([...document.body.children].map((el) => el.id)).toEqual(["root", "dialog"]);
  });

  it("leaves a nested element of the same name alone", () => {
    // Only a child of the body is mermaid's leftover. Anything of that name deeper in the tree
    // belongs to whatever put it there.
    const app = document.createElement("div");
    app.id = "root";
    app.innerHTML = '<div id="dma-9"></div>';
    document.body.append(app);
    sweepOrphans();
    expect(document.getElementById("dma-9")).not.toBeNull();
  });

  it("is fine when there is nothing to do", () => {
    expect(() => sweepOrphans()).not.toThrow();
  });
});

describe("somewhere of our own to render in", () => {
  it("is off screen, and not display:none", () => {
    // A diagram's layout is measured text, and a `display: none` subtree has no measurements —
    // every label would come out the same size.
    const target = renderTarget();
    expect(target.style.visibility).toBe("hidden");
    expect(target.style.display).not.toBe("none");
    expect(target.style.left.startsWith("-")).toBe(true);
  });

  it("is one element, however many diagrams there are", () => {
    expect(renderTarget()).toBe(renderTarget());
    expect(document.body.querySelectorAll('[data-slot="kith_mermaid_scratch"]')).toHaveLength(1);
  });

  it("is empty before every render, whatever the last one left in it", () => {
    const target = renderTarget();
    target.innerHTML = '<div id="dkith-diagram-1"><svg class="error-icon"></svg></div>';
    expect(renderTarget().children).toHaveLength(0);
  });

  it("comes back if something removes it", () => {
    renderTarget().remove();
    expect(renderTarget().isConnected).toBe(true);
  });
});

/**
 * Every kind of drawing has to look like it came from here.
 *
 * mermaid themes flowcharts from `themeVariables` and then, for several diagram types, quietly
 * does not: `xychart` falls back to a hardcoded `#3498db`, quadrant and timeline to palettes of
 * their own. Nobody notices while the only thing anyone draws is boxes and arrows — and the
 * persona had taught exactly that, listing "a flow, a sequence, a state machine" as what counts
 * as a shape. The moment it also says to reach for a chart, an unthemed chart is what ships:
 * stock blue on cream, looking pasted in from another application.
 */
describe("a drawing that is not a flowchart", () => {
  const stock = ["#3498db", "#ECECFF", "#FFF4DD", "#ff0000", "#4d4d4d"];

  it("is coloured from the same palette as everything else", () => {
    for (const dark of [false, true]) {
      const vars = mermaidConfig(dark).themeVariables as Record<string, never>;
      const palette = (vars.xyChart as unknown as { plotColorPalette: string }).plotColorPalette;
      expect(palette.split(",")).toHaveLength(6);
      for (const colour of stock) expect(palette.toLowerCase()).not.toContain(colour.toLowerCase());
      // The same six, in the same order, as the pie — one set of numbers shown two ways in one
      // reply should not change colour between them.
      expect(palette.split(",")[0]).toBe(vars.pie1);
      expect(palette.split(",")[1]).toBe(vars.pie2);
    }
  });

  it("has its axes and labels themed, not left to the default", () => {
    const vars = mermaidConfig(false).themeVariables as Record<string, never>;
    const chart = vars.xyChart as unknown as Record<string, string>;
    for (const key of ["titleColor", "xAxisLabelColor", "yAxisLineColor", "dataLabelColor"]) {
      expect(chart[key], key).toBeTruthy();
    }
    expect(vars.quadrantPointFill).toBeTruthy();
    expect(vars.cScale0).toBeTruthy();
  });

  it("is told to fit the column, like the flowchart already was", () => {
    // Without this each renders at its own fixed pixel width, so a chart wider than the thread
    // is clipped rather than scaled — which reads as broken, not as narrow.
    const config = mermaidConfig(false) as unknown as Record<string, { useMaxWidth?: boolean }>;
    for (const kind of ["flowchart", "sequence", "xyChart", "pie", "quadrantChart", "timeline"]) {
      expect(config[kind]?.useMaxWidth, kind).toBe(true);
    }
  });
});
