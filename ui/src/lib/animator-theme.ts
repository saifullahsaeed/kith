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
 *
 * And then the synonyms, which are here because of a real one: he wrote `color: orange` and lost
 * a whole animated diagram to it. `amber` is the library's word for that colour, not anybody
 * else's, and being refused for using the ordinary name of a colour that is *right there in the
 * palette* is the vocabulary being pedantic rather than helpful. So the obvious other word for
 * each hue is a name for it too. This is not the safety net — the component forgives a name
 * nothing here has ever heard of and says so — it is the list of words that are simply correct.
 */
import type { Theme } from "mermaid-animator";

import { PALETTE } from "@/lib/kith-palette";

/** How a node in a given state is drawn. Read off `Theme` rather than imported, because the
 *  library declares the type and does not export it from its entry point. */
type NodeStateStyle = NonNullable<Theme["states"]>[string];

/** The eight names `FLOW_SYNTAX` tells him he may use, in Kith's register.
 *
 *  Light values are deeper than dark ones for the ordinary reason: a packet has to hold its own
 *  against cream at one end and against near-black at the other. */
const HUES = {
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

/** The other word for each of those, pointing at the same value. Chosen for what a person — or
 *  he — reaches for when they mean that colour and do not happen to know the library's name for
 *  it. `orange` is the one that cost a diagram; the rest are the same mistake waiting. */
const SAME_COLOUR: Record<string, keyof (typeof HUES)["light"]> = {
  orange: "amber",
  gold: "yellow",
  teal: "cyan",
  violet: "purple",
  magenta: "pink",
  lime: "green",
  crimson: "red",
};

function flowColours(dark: boolean): Record<string, string> {
  const hues: Record<string, string> = { ...HUES[dark ? "dark" : "light"] };
  for (const [word, hue] of Object.entries(SAME_COLOUR)) hues[word] = hues[hue];
  return hues;
}

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

/** And the words for those three that are not those three words. A node is failed, fine, or
 *  working on it; `error`/`ok`/`busy` are one spelling of that and not the obvious one. */
const SAME_STATE: Record<string, keyof (typeof STATES)["light"]> = {
  failed: "error",
  failure: "error",
  down: "error",
  broken: "error",
  healthy: "ok",
  success: "ok",
  up: "ok",
  done: "ok",
  warning: "busy",
  pending: "busy",
  active: "busy",
  working: "busy",
  waiting: "busy",
};

function nodeStates(dark: boolean): Record<string, NodeStateStyle> {
  const half = STATES[dark ? "dark" : "light"];
  const states: Record<string, NodeStateStyle> = { ...half };
  for (const [word, state] of Object.entries(SAME_STATE)) states[word] = half[state];
  return states;
}

/** What a name nobody defined is drawn as, when the component decides to draw it anyway rather
 *  than refuse the diagram. The default packet colour, which is to say: it moves, and it looks
 *  like this app. */
export function defaultFlowColour(dark: boolean): string {
  return HUES[dark ? "dark" : "light"].amber;
}

/** Fresh objects every call, `flowColors` and `states` included. The component extends them with
 *  whatever name he invented, and a shared constant would keep that name for the rest of the
 *  session. */
export function animatorTheme(dark: boolean): Theme {
  const c = PALETTE[dark ? "dark" : "light"];
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
    flowColors: flowColours(dark),
    states: nodeStates(dark),
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
