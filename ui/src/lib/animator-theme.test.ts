/**
 * The half of the theme that is a vocabulary rather than a colour.
 *
 * `flowColors` and `states` are the names he is allowed to write in a flow script, and the
 * library treats a name it does not hold as a hard error — it does not fall back to a default
 * colour, it refuses the script, and the whole animation is lost with it. So a theme that
 * renames or drops one of the built-in names turns a correct script into a broken one, and the
 * failure arrives as "the diagram stopped animating" long after the change that caused it.
 *
 * These check that against the library's own validator rather than against a list copied out of
 * its README, so the two cannot drift apart quietly.
 */
import { validateFlow } from "mermaid-animator";
import { describe, expect, it } from "vitest";

import { animatorTheme } from "@/lib/animator-theme";
import { PALETTE } from "@/lib/kith-palette";

/** Every colour name the library documents, which is every name `FLOW_SYNTAX` — the description
 *  Kith is given — tells him he may use. */
const COLORS = ["amber", "red", "green", "blue", "cyan", "purple", "pink", "yellow"];
const STATES = ["error", "ok", "busy"];

const routeIn = (color: string) =>
  `---\nflow:\n  loop:\n    - route: [A, B]\n      color: ${color}\n---\nflowchart LR\n  A --> B`;

const stateIn = (state: string) =>
  `---\nflow:\n  loop:\n    - state: { A: ${state} }\n---\nflowchart LR\n  A --> B`;

/** What the component hands the validator: the names this theme carries, in place of the
 *  built-in ones. */
function vocabulary(dark: boolean) {
  const theme = animatorTheme(dark);
  return {
    colors: Object.keys(theme.flowColors ?? {}),
    states: Object.keys(theme.states ?? {}),
  };
}

describe.each([
  ["light", false],
  ["dark", true],
])("the %s theme", (_name, dark) => {
  it("defines every colour a flow script is allowed to name", () => {
    for (const color of COLORS) {
      // Accepted by the library on its own, so this is a name he will be told about…
      expect(validateFlow(routeIn(color))).toMatchObject({ ok: true });
      // …and accepted when the diagram is rendered with Kith's theme instead.
      expect(validateFlow(routeIn(color), vocabulary(dark))).toMatchObject({ ok: true });
    }
  });

  it("defines every node state a flow script is allowed to name", () => {
    for (const state of STATES) {
      expect(validateFlow(stateIn(state))).toMatchObject({ ok: true });
      expect(validateFlow(stateIn(state), vocabulary(dark))).toMatchObject({ ok: true });
    }
  });

  it("still refuses a name nobody defined", () => {
    // The behaviour the two tests above exist because of, asserted so that a green run means
    // "the names match" rather than "the validator says yes to everything".
    expect(validateFlow(routeIn("turquoise"), vocabulary(dark))).toMatchObject({
      ok: false,
      code: "UNKNOWN_COLOR",
    });
    expect(validateFlow(stateIn("melting"), vocabulary(dark))).toMatchObject({
      ok: false,
      code: "UNKNOWN_STATE",
    });
  });

  it("draws the diagram in Kith's colours, not the library's", () => {
    const theme = animatorTheme(dark);
    const c = PALETTE[dark ? "dark" : "light"];
    // With a flow script the animator repaints every node and edge with this one colour and
    // ignores `edgeColors` entirely, so this is the whole resting look of the drawing.
    expect(theme.nodeBorderDefault).toBe(c.accent);
    expect(theme.background).toBe(c.background);
    expect(theme.popoverBackground).toBe(c.surface);
    expect(theme.popoverText).toBe(c.text);
    expect(theme.popoverBorder).toBe(c.line);
    // Agrees with `mermaidConfig`, which is the thing that actually initialises mermaid.
    expect(theme.mermaidTheme).toBe("base");
  });
});

it("is a different theme in each mode", () => {
  // A single object shared by both halves would look right in whichever one it was written for.
  expect(animatorTheme(true)).not.toEqual(animatorTheme(false));
  expect(animatorTheme(true).flowColors?.amber).toBe(PALETTE.dark.accent);
  expect(animatorTheme(false).flowColors?.amber).toBe(PALETTE.light.accent);
  expect(animatorTheme(true).flowColors?.green).toBe(PALETTE.dark.second);
  expect(animatorTheme(false).flowColors?.green).toBe(PALETTE.light.second);
});
