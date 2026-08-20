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
  const selector = RENDER_IDS.map((id) => `:scope > [id^="d${id}"]`).join(", ");
  for (const orphan of document.body.querySelectorAll(selector)) orphan.remove();
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
