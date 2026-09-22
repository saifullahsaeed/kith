/**
 * How big the screen is — declared by the design, not assumed by the surface.
 *
 * Everything here used to be one constant: `DESIGN_W = 1200`, with a fixed 560-tall clipped
 * box on the board. Which meant a design had no way to be a phone. A mobile screen rendered as
 * a 1200-wide desktop page and was then scaled down, so the columns that would have stacked
 * did not, the type came out microscopic, and the thing the person looked at was not the thing
 * being designed. In the other direction a short screen got the same tall box as a long one and
 * arrived with a third of the frame empty below it, which reads as unfinished work.
 *
 * It also left the model with no way to *know*. Asked for "the settings screen" it has to pick
 * a width to write against and nothing ever told it which, so it guessed — and the guess was
 * invisible, because the surface imposed 1200 whatever it had guessed.
 *
 * So a design says:
 *
 *     <body data-k-frame="mobile">          a named preset
 *     <body data-k-frame="1440x900">        or exact pixels
 *
 * and everything downstream — the editor, the board's aspect, the checker's render and its
 * screenshots — takes its size from that one declaration.
 */

export interface Frame {
  width: number;
  height: number;
  /** The preset's name, or "custom", or "default" when the design did not say. */
  name: string;
}

/**
 * The sizes worth naming.
 *
 * Deliberately few. A list long enough to need scrolling is a list somebody picks the first
 * entry from, and the point of a name is that `data-k-frame="mobile"` reads as an intention
 * where `390x844` reads as a measurement.
 */
export const PRESETS: Record<string, { width: number; height: number }> = {
  desktop: { width: 1440, height: 900 },
  laptop: { width: 1280, height: 800 },
  tablet: { width: 834, height: 1112 },
  mobile: { width: 390, height: 844 },
};

/**
 * What a design that does not declare a frame gets.
 *
 * 1200x800 because 1200 is what every design on this machine was implicitly authored against
 * before the attribute existed. Changing the default would resize work nobody touched.
 */
export const DEFAULT_FRAME: Frame = { width: 1200, height: 800, name: "default" };

/** Sane bounds. A frame is a screen, not a billboard and not a favicon. */
const MIN = 240;
const MAX = 4000;

/** `data-k-frame`, read. Anything unrecognised falls back rather than failing the design. */
export function readFrame(declared: string | null | undefined): Frame {
  const text = String(declared ?? "").trim().toLowerCase();
  if (!text) return DEFAULT_FRAME;

  const preset = PRESETS[text];
  if (preset) return { ...preset, name: text };

  const exact = /^(\d{2,4})\s*[x×]\s*(\d{2,4})$/.exec(text);
  if (exact) {
    const width = Number(exact[1]);
    const height = Number(exact[2]);
    if (width >= MIN && width <= MAX && height >= MIN && height <= MAX) {
      return { width, height, name: "custom" };
    }
  }
  return DEFAULT_FRAME;
}

/** How the frame reads in a sentence, for the editor's chrome. */
export function describeFrame(frame: Frame): string {
  return frame.name === "custom" || frame.name === "default"
    ? `${frame.width}×${frame.height}`
    : `${frame.name} · ${frame.width}×${frame.height}`;
}

/**
 * `data-k-frame` lifted straight out of source text.
 *
 * The board holds each design as a string and scales dozens of them on every pointer move; a
 * full `parseDesign` per frame per render is the wrong cost for one attribute. It reads the
 * same two places `parseDesign` does — `<body>`, then the outermost element — by looking at
 * the first occurrence, which is what both of those are.
 */
export function frameAttribute(html: string): string | null {
  const hit = /data-k-frame\s*=\s*["']([^"']{1,40})["']/i.exec(html || "");
  return hit ? hit[1] : null;
}
