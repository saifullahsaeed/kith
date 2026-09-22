/**
 * Getting a parsed design onto the screen without letting it out of its box.
 *
 * `parse.ts` decides what is allowed to exist. This decides how what survived is handed to
 * React, and the two jobs are separated because only one of them is a security boundary — an
 * error here makes a design look wrong, an error there makes it dangerous.
 *
 * The one thing here that *is* containment is `scopeStyleSheet`. His stylesheet is real CSS in
 * the same document as the app, so a rule of his and a rule of ours are in the same cascade.
 * Scoping is what keeps `.card { display: none }` a statement about his design rather than about
 * the page it is sitting in. `safeSelector` already refused the selectors that would defeat this
 * — `html`, `body`, `:root`, `*` — so the two halves are doing one job from opposite ends.
 */
import type { CSSProperties } from "react";

/**
 * A declaration map as React wants it.
 *
 * Custom properties are passed through untouched — React writes anything beginning `--`
 * straight to the style attribute, which is what makes `var(--kith-accent)` resolve against the
 * app's own tokens and a design follow a theme switch for free.
 */
export function styleToReact(style: Record<string, string>): CSSProperties {
  const out: Record<string, string> = {};
  for (const [property, value] of Object.entries(style)) {
    if (property.startsWith("--")) out[property] = value;
    else out[property.replace(/-([a-z])/g, (_, c: string) => c.toUpperCase())] = value;
  }
  return out as CSSProperties;
}

/** The reverse, for writing a patch back into the source text. */
export function styleToCss(style: Record<string, string>): string {
  return Object.entries(style)
    .map(([property, value]) => `${property}: ${value}`)
    .join("; ");
}

/**
 * Every rule in the sheet, confined to one subtree.
 *
 * Prefixes rather than wraps, because `@scope` is not everywhere yet and a nesting selector
 * would need his rules rewritten anyway. `@media` blocks are recursed into so the rules inside
 * them are scoped too — a media query is the one at-rule that survives sanitising, and leaving
 * its body unscoped would be a hole exactly the size of "he wrote a responsive design".
 */
export function scopeStyleSheet(sheet: string, scope: string): string {
  const out: string[] = [];
  let i = 0;

  while (i < sheet.length) {
    const open = sheet.indexOf("{", i);
    if (open < 0) break;
    const prelude = sheet.slice(i, open).trim();

    let depth = 1;
    let j = open + 1;
    for (; j < sheet.length && depth > 0; j++) {
      if (sheet[j] === "{") depth++;
      else if (sheet[j] === "}") depth--;
    }
    const body = sheet.slice(open + 1, j - 1);
    i = j;

    if (prelude.startsWith("@")) {
      out.push(`${prelude} {\n${scopeStyleSheet(body, scope)}\n}`);
      continue;
    }

    const selectors = prelude
      .split(",")
      .map((one) => one.trim())
      .filter(Boolean)
      // `:scope` is the design root itself — what `body { }` became in `sanitiseStyleSheet` —
      // so it is replaced rather than prefixed. Prefixed, it would name a `:scope` *inside* the
      // root, which matches nothing, and the design's page box and tokens would vanish again.
      .map((one) =>
        one === ":scope" ? scope : one.startsWith(":scope ") ? `${scope}${one.slice(6)}` : `${scope} ${one}`,
      )
      .join(", ");
    out.push(`${selectors} { ${body.trim()} }`);
  }

  return out.join("\n");
}

/** Tags the DOM will not let you give children to. */
export const VOID_TAGS = new Set(["img", "br", "hr", "input", "use", "stop", "path", "circle", "ellipse", "rect", "line", "polyline", "polygon"]);

/** Tags React needs to create in the SVG namespace rather than the HTML one. */
export const SVG_TAGS = new Set([
  "svg", "g", "path", "circle", "ellipse", "rect", "line", "polyline", "polygon", "text",
  "tspan", "defs", "use", "clippath", "mask", "lineargradient", "radialgradient", "stop",
]);

/** The casing SVG actually wants, which JSX does not fix for you on a created element. */
const SVG_CASE: Record<string, string> = {
  viewbox: "viewBox",
  clippath: "clipPath",
  lineargradient: "linearGradient",
  radialgradient: "radialGradient",
  gradientunits: "gradientUnits",
  preserveaspectratio: "preserveAspectRatio",
};

/** The element name to create, with SVG's capitalisation restored. */
export function elementName(tag: string): string {
  return SVG_CASE[tag] ?? tag;
}

/**
 * Attributes as React props.
 *
 * React accepts hyphenated SVG presentation attributes (`stroke-width`) as-is, so those are left
 * alone; the ones that need fixing are the camel-cased element and attribute names the HTML
 * parser lowercased on the way in.
 */
export function attrsToProps(attrs: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [name, value] of Object.entries(attrs)) {
    if (name === "class") continue;
    out[SVG_CASE[name] ?? name] = value;
  }
  return out;
}
