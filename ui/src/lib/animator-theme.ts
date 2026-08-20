/**
 * Kith's palette, in the shape `mermaid-animator` wants it.
 *
 * Two things are being decided here, and they are worth keeping apart.
 *
 * **The drawing.** With a flow script the animator repaints the whole diagram *neutral* — every
 * node and edge takes `nodeBorderDefault`, and its own vibrant per-node palette is not used at
 * all. That is the behaviour we want and the reason the still and moving versions of a fence
 * look like the same picture: the accent outline and its soft fill, exactly what
 * `themeVariables` gives the still one. What the animation adds is motion, not a recolour.
 * (The fill is `nodeBorderDefault` darkened to 30% and laid on at `nodeFillOpacity`, which is
 * why the two opacities differ so much between the themes — the same darkening reads as a tint
 * on cream and as a body colour on near-black.)
 *
 * **The vocabulary.** `flowColors` and `states` are not decoration: a name he writes that this
 * object does not define is a hard error, not a fall back to a default colour, and the error
 * takes the whole animation with it. So every built-in name — all eight colours, all three
 * states — has to be here whether or not Kith's palette has a hue for it. Two of them are the
 * palette (`amber` is the accent, `green` is the second); the rest are chosen to sit in the same
 * warm, low-saturation register rather than borrowed from the library's brighter set, and to
 * stay apart from each other when two packets are on screen at once.
 */
import type { Theme } from "mermaid-animator";

import { PALETTE } from "@/lib/kith-palette";

/** The eight names `FLOW_SYNTAX` tells him he may use, in Kith's register.
 *
 *  Light values are deeper than dark ones for the ordinary reason: a packet has to hold its own
 *  against cream at one end and against near-black at the other. */
const FLOW_COLORS = {
  light: {
    amber: "#c77618",
    yellow: "#a1820c",
    red: "#c0392b",
    green: "#0e8c41",
    cyan: "#0f8fa0",
    blue: "#2b6cb0",
    purple: "#7c5cbf",
    pink: "#b8437f",
  },
  dark: {
    amber: "#f1b65a",
    yellow: "#e0c766",
    red: "#f08a7a",
    green: "#5dd786",
    cyan: "#63cddb",
    blue: "#7fb3f0",
    purple: "#b79bf0",
    pink: "#f094c0",
  },
} as const;

/** The three built-in node states, drawn from the same three colours a person already reads as
 *  failed, fine and working. */
const STATES = {
  light: {
    error: { stroke: "#c0392b", fill: "#c0392b" },
    ok: { stroke: "#0e8c41", fill: "#0e8c41" },
    busy: { stroke: "#c77618", fill: "#c77618" },
  },
  dark: {
    error: { stroke: "#f08a7a", fill: "#f08a7a" },
    ok: { stroke: "#5dd786", fill: "#5dd786" },
    busy: { stroke: "#f1b65a", fill: "#f1b65a" },
  },
} as const;

export function animatorTheme(dark: boolean): Theme {
  const c = PALETTE[dark ? "dark" : "light"];
  const half = dark ? "dark" : "light";
  return {
    name: dark ? "kith-dark" : "kith",
    // Only read when a still frame is exported, where a transparent ground would come out as a
    // black rectangle in whatever it is pasted into.
    background: c.background,
    // Agrees with `mermaidConfig`, which is what actually initialises mermaid. Stated rather
    // than left at the library's `dark`/`default` so the two are not quietly saying different
    // things about the same render.
    mermaidTheme: "base",
    // Unused while a flow script is present — the neutral repaint ignores it — and one entry
    // rather than a spread so that an ambient diagram, if one ever gets here, still looks like
    // this app instead of like a network graph.
    edgeColors: [c.dim],
    flowColors: FLOW_COLORS[half],
    states: STATES[half],
    // What everything off the active route fades to. The point of a flow script is that you can
    // see where the packet is, and the library's own defaults are already the right depth.
    dimOpacity: dark ? 0.15 : 0.2,
    dotGlowOpacity: dark ? 0.35 : 0.22,
    nodeStrokeWidth: 1.5,
    nodeFillOpacity: dark ? 0.4 : 0.07,
    nodeBorderDefault: c.accent,
    clusterStrokeWidth: 1,
    clusterFillOpacity: 0.04,
    // A container is a grouping, not a thing to look at. Kith's still diagrams give it the
    // faintest line in the palette; here it can only be the accent, so it is the accent turned
    // most of the way down.
    clusterBorderOpacity: 0.35,
    popoverBackground: c.surface,
    popoverText: c.text,
    popoverBorder: c.line,
    popoverIdColor: c.accent,
    popoverSecondaryText: c.dim,
  };
}
