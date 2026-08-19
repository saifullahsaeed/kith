/**
 * Kith's own colours, as literal hex.
 *
 * Lifted out of `mermaid-diagram.tsx` when a second surface needed them: the sealed canvas
 * frame is a document of its own, so it cannot inherit `index.css` through the cascade the way
 * everything else in this app does — the palette has to be handed to it as text.
 *
 * Baked rather than read from the live stylesheet, for the reason the diagram already
 * documented: these mirror `--background`, `--foreground`, `--muted`, `--border`, `--kith` and
 * `--roam`, and getting a usable value back out of `getComputedStyle` for an oklch token is
 * browser-dependent in a way that fails silently and looks like a theming bug.
 *
 * If the tokens in `index.css` change, these want changing with them.
 */
export const PALETTE = {
  light: {
    background: "#fbf9f5",
    surface: "#fffefb",
    line: "#e4e0dc",
    text: "#29231d",
    dim: "#69625b",
    accent: "#c77618",
    accentSoft: "#f4e7d6",
    second: "#0e8c41",
    secondSoft: "#daeadc",
    muted: "#f1eee9",
  },
  dark: {
    background: "#110e0b",
    surface: "#1a1713",
    line: "#25221e",
    text: "#eae5dd",
    dim: "#a19a92",
    accent: "#f1b65a",
    accentSoft: "#352918",
    second: "#5dd786",
    secondSoft: "#1d2e1f",
    muted: "#26221f",
  },
} as const;

/** Widened off the literals `as const` produces, so the two halves are one type rather than
 *  two incompatible ones. */
export type Palette = Record<keyof (typeof PALETTE)["light"], string>;

/** The half of the palette a given theme is using. */
export function paletteFor(dark: boolean): Palette {
  return PALETTE[dark ? "dark" : "light"];
}
