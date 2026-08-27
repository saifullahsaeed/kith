/**
 * How mermaid is configured to draw a Kith diagram.
 *
 * Lifted out of `mermaid-diagram.tsx` for the same reason `kith-palette.ts` was lifted out of it
 * before: a second surface needs it. An animated fence is drawn by `mermaid-animator`, which
 * calls `mermaid.initialize` itself — and there is only one mermaid. Two callers holding two
 * opinions about `themeVariables` means whichever initialised last wins, so a message with a
 * still diagram and a moving one would draw them in different colours, and which one got Kith's
 * palette would depend on the order two promises happened to settle.
 *
 * One function, handed to both. The animator takes it through its `mermaid` option, which it
 * spreads *after* its own defaults, so this is the copy with authority in both paths.
 */
import type { MermaidConfig } from "mermaid";

import { PALETTE } from "@/lib/kith-palette";

/** Loaded once, on the first diagram anyone sees.
 *
 *  Mermaid is about a megabyte of parser and layout engine, and most conversations contain no
 *  diagram at all — a static import would put it in the entry chunk of every session that never
 *  draws one. The promise is module-level so a message with six diagrams loads it once rather
 *  than six times, and so both renderers share the one instance they are both configuring.
 *
 *  Here rather than in the component that used to own it because the animated path needs it too,
 *  for `parse` — see `sweepOrphans` for what happens when a diagram reaches `render` without it. */
let engine: Promise<(typeof import("mermaid"))["default"]> | null = null;

export function mermaidEngine() {
  engine ??= import("mermaid").then((mod) => mod.default);
  return engine;
}

/** The ids the two renderers pass to `mermaid.render`. Mermaid's scratch element is `d` plus the
 *  id it was given, so these are also how its leftovers are recognised. */
const RENDER_IDS = ["kith-diagram-", "ma-"];

/**
 * Somewhere of our own for mermaid to work in.
 *
 * `mermaid.render` takes a container as its third argument and, given none, *appends its scratch
 * element to `<body>`* — which is where every version of this bug comes from. On failure it draws
 * its own error graphic into that element through the ordinary render path, then throws and
 * leaves it there: a cartoon bomb the size of a paragraph reading "Syntax error in text", parked
 * at the bottom of the window under the composer, attached to nothing.
 *
 * So it is given a container: one element, ours, made once, off screen, emptied before every
 * render. Whatever mermaid does in there — draw a diagram, draw a bomb, leave the scratch element
 * behind — happens somewhere nobody can see and gets cleared by the next render regardless.
 * That is containment by construction rather than by cleaning up afterwards, which is the only
 * kind that holds for a failure nobody predicted.
 *
 * Off screen rather than `display: none`, because a diagram's layout is measured text and a
 * `display: none` subtree has no measurements — every label would come out the same size. Full
 * width, because that is what it had when it was parented to the body, and a narrower box would
 * wrap labels differently from every diagram drawn before this.
 *
 * The one thing it cannot cover is a render *inside* `mermaid-animator`, which calls `render`
 * itself and passes no container. That path is guarded by parsing first and swept when it throws
 * — see `sweepOrphans`.
 */
let scratch: HTMLDivElement | null = null;

export function renderTarget(): HTMLElement {
  if (!scratch || !scratch.isConnected) {
    scratch = document.createElement("div");
    scratch.dataset.slot = "kith_mermaid_scratch";
    scratch.setAttribute("aria-hidden", "true");
    scratch.style.cssText =
      "position:absolute;left:-99999px;top:0;width:100%;visibility:hidden;pointer-events:none";
    document.body.append(scratch);
  }
  // Whatever the last render left, wanted or not.
  scratch.textContent = "";
  return scratch;
}

/** Is this thing at the top of the body one of mermaid's leftovers?
 *
 *  Two rules, and the second is the one that matters. The first is exact — `d` plus an id one of
 *  our renderers issued — and would stop working the day mermaid changes how it names its
 *  scratch element, silently, with the bomb reappearing. The second recognises the *graphic*:
 *  the error icon it draws and the words it writes, neither of which anything in this app has
 *  any other reason to contain.
 *
 *  Only ever asked about a direct child of `<body>`, and only after the render that could have
 *  put it there has settled. Both halves of that matter: dialogs, popovers and the diagram's own
 *  full-screen view are all portalled to the body too, and mermaid's scratch element is the same
 *  element whether a render is using it or has abandoned it — so removing one by name while a
 *  render is still in flight breaks every diagram in the app. Which it did, once, for about ten
 *  minutes. */
function isLeftover(element: Element): boolean {
  const id = element.id || "";
  if (id.startsWith("d") && RENDER_IDS.some((prefix) => id.slice(1).startsWith(prefix))) return true;
  if (element.querySelector(".error-icon, .error-text")) return true;
  return (element.textContent || "").includes("Syntax error in text");
}

/**
 * Whatever mermaid left in the body when a render went wrong.
 *
 * `render` builds its diagram in a temporary `#d{id}` element parented to `<body>`, and on
 * failure it draws its *own* error graphic into that element and rethrows — leaving the thing
 * behind. That graphic is a cartoon bomb the size of a paragraph reading "Syntax error in text",
 * and because it hangs off the body rather than the conversation it lands at the bottom of the
 * window, under the composer, attached to nothing. Two bad diagrams, two bombs.
 *
 * The still renderer avoids the whole thing by parsing first, which is why this went unnoticed
 * for as long as it did. The animated path renders inside the library, which does not parse
 * first — so it needs the guard *and* this, because a diagram can parse and still fail to lay
 * out, and the difference between those two is not knowable from here.
 */
export function sweepOrphans(): void {
  for (const child of [...document.body.children]) {
    if (isLeftover(child)) child.remove();
  }
}

/** The `base` theme with every colour it derives from replaced.
 *
 *  `base` rather than `dark`/`neutral` because it is the only one mermaid means to be
 *  overridden — the named themes hardcode shades that ignore half of what you pass. */
export function themeVariables(dark: boolean) {
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

    // Charts with an axis, which had been left to mermaid's own taste — `xychart` falls back
    // to a hardcoded `#3498db`, a stock blue belonging to no theme here, so a bar chart came
    // out looking like it had been pasted in from another application. These are the same six
    // colours the pie above uses, in the same order, so a set of numbers shown twice in one
    // reply is the same colour both times.
    xyChart: {
      backgroundColor: "transparent",
      titleColor: c.text,
      dataLabelColor: c.text,
      xAxisLabelColor: c.dim,
      xAxisTitleColor: c.text,
      xAxisTickColor: c.line,
      xAxisLineColor: c.line,
      yAxisLabelColor: c.dim,
      yAxisTitleColor: c.text,
      yAxisTickColor: c.line,
      yAxisLineColor: c.line,
      plotColorPalette: [c.accent, c.second, c.dim, c.accentSoft, c.secondSoft, c.muted].join(
        ",",
      ),
    },

    // Sankey and quadrant reach for their own palettes the same way.
    quadrant1Fill: c.accentSoft,
    quadrant2Fill: c.secondSoft,
    quadrant3Fill: c.muted,
    quadrant4Fill: c.background,
    quadrantPointFill: c.accent,
    quadrantPointTextFill: c.text,
    quadrantTitleFill: c.text,
    quadrantXAxisTextFill: c.dim,
    quadrantYAxisTextFill: c.dim,
    quadrantInternalBorderStrokeFill: c.line,
    quadrantExternalBorderStrokeFill: c.line,

    // Timeline and journey, which are `cScale0…N` underneath.
    cScale0: c.accent,
    cScale1: c.second,
    cScale2: c.dim,
    cScale3: c.accentSoft,
    cScale4: c.secondSoft,
    cScale5: c.muted,
    cScaleLabel0: c.background,
    cScaleLabel1: c.background,
    cScaleLabel2: c.background,
    cScaleLabel3: c.text,
    cScaleLabel4: c.text,
    cScaleLabel5: c.text,
  };
}

/** Everything `mermaid.initialize` is told, in either renderer. */
export function mermaidConfig(dark: boolean): MermaidConfig {
  return {
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
    //
    // The animated path needs it for a second reason: the animator resolves a route by
    // reading the labels out of the drawing, and a label locked inside a foreignObject is
    // one it cannot match a node name against.
    htmlLabels: false,
    flowchart: { curve: "basis", padding: 14, useMaxWidth: true, htmlLabels: false },
    sequence: { useMaxWidth: true, actorMargin: 40 },
    gantt: { useMaxWidth: true },
    // The rest of what he can draw, told to fit the column like the three above. Without it
    // each renders at its own fixed pixel width and a chart wider than the thread is clipped
    // rather than scaled — which reads as a broken diagram, not a narrow one.
    xyChart: { useMaxWidth: true },
    pie: { useMaxWidth: true },
    quadrantChart: { useMaxWidth: true },
    sankey: { useMaxWidth: true },
    timeline: { useMaxWidth: true },
    journey: { useMaxWidth: true },
    mindmap: { useMaxWidth: true },
    er: { useMaxWidth: true },
    state: { useMaxWidth: true },
    class: { useMaxWidth: true },
    block: { useMaxWidth: true },
  };
}

/** The same object, in the shape `mermaid-animator`'s option bag declares.
 *
 *  It hands the value straight to `mermaid.initialize`, but types it as a loose record — and
 *  `MermaidConfig` is a closed interface, so it does not satisfy one. One cast, here, rather
 *  than at each of the two call sites. */
export function mermaidOptions(dark: boolean): Record<string, unknown> {
  return mermaidConfig(dark) as unknown as Record<string, unknown>;
}
