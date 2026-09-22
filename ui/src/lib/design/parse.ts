/**
 * His HTML, turned into something with handles on it — and the whitelist that makes that safe.
 *
 * ── Why this is not the canvas ──────────────────────────────────────────────────────────────
 *
 * `canvas.ts` contains a page by giving it an opaque origin: nothing inside can reach the app,
 * so nothing inside has to be read. That is the cheaper and stronger boundary and it stays
 * exactly where it is, for every ```html fence that is not a design.
 *
 * A design cannot use it. You cannot draw a selection box on a document you are forbidden to
 * touch — `allow-same-origin` is absent precisely so `contentDocument` throws — and an editor
 * whose whole job is measuring and mutating nodes needs to be in the same document as them. So a
 * design renders in trusted React, and the containment moves from the origin to this file.
 *
 * That is a real trade and it is made here, once. Everything below is a whitelist: a tag, an
 * attribute, a CSS property or a CSS function that is not named is not escaped or rewritten, it
 * is dropped. The tests are adversarial for that reason — the failure mode of a whitelist is not
 * "it rejected something useful", it is "somebody added a convenience to it".
 *
 * ── Why a value is rebuilt rather than checked ──────────────────────────────────────────────
 *
 * Substring checks on CSS lose to CSS's own syntax: `u\72 l(...)` is `url(...)` after the
 * tokeniser unescapes it, and `re/**\/d` is `red` after comments are removed. So values are not
 * scanned for bad content — they are refused unless they are made entirely of things known to be
 * inert. A backslash or a comment marker anywhere in a value means the value does not survive,
 * because there is no reason for either to appear in a design and every reason for one to appear
 * in an attack.
 */

import { type Frame, readFrame } from "@/lib/design/frame";

/** A node the editor can select, measure and patch. */
export interface DesignNode {
  /** `data-k`, or minted in document order. Every patch addresses one of these. */
  id: string;
  tag: string;
  attrs: Record<string, string>;
  /** Inline declarations, whitelisted and re-serialised. The editable surface. */
  style: Record<string, string>;
  classes: string[];
  /** The node's own text, when it has any of its own. */
  text?: string;
  /**
   * Text and children in the order the file has them, for a node that has both — strings are
   * text, numbers index `children`. Absent for the ordinary node that has one or the other.
   *
   * `text` alone rendered before every child, so `<span>Arrived <strong>9:31</strong></span>`
   * came out "Arrived9:31" and `<span><strong>10:30</strong> Open slot</span>` came out
   * "Open slot10:30": the space and the order both lost, on the board and nowhere else.
   */
  segments?: Array<string | number>;
  children: DesignNode[];
  /** Opted into free positioning with `data-k-absolute`, so drag means x/y here. */
  absolute: boolean;
  /**
   * `data-k-component` — the name of the inventory part this node is an instance of, when it
   * says it is one. Empty for the ordinary case of a node that is just a node.
   *
   * Read rather than dropped because the alternative is worse than losing the link: the
   * whitelist reports what it drops into the editor's warning strip, so a screen composed
   * correctly out of the project's own components would have arrived covered in warnings about
   * doing it.
   */
  component: string;
}

export interface ParsedDesign {
  root: DesignNode;
  /** How big the screen is, as the design declared it. See `frame.ts`. */
  frame: Frame;
  /** His `<style>`, sanitised. Rendered, never written to — see the spec's Layer 0. */
  sheet: string;
  /** What did not survive, in words, for the layout report and the editor's warning strip. */
  dropped: string[];
}

/**
 * Tags whose children are not content.
 *
 * The distinction that matters: an unknown tag is *unwrapped* — its children are kept, because a
 * `<form>` wrapped round a layout is markup habit and dropping the subtree would silently delete
 * half a design. These are different. A script's children are code, a template's are inert
 * markup nobody asked to render, and keeping either would be keeping the thing we removed the
 * parent for.
 */
export const DROP_SUBTREE = new Set(["script", "noscript", "template", "link", "meta", "base", "title"]);

/** Everything a mocked-up screen is made of. Anything else is unwrapped, not rendered. */
export const ALLOWED_TAGS = new Set([
  "div", "span", "p", "a", "img", "button", "input", "textarea", "select", "option", "label",
  "h1", "h2", "h3", "h4", "h5", "h6",
  "ul", "ol", "li", "dl", "dt", "dd",
  "table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption",
  "section", "article", "header", "footer", "nav", "aside", "main",
  "figure", "figcaption", "blockquote", "pre", "code", "strong", "em", "b", "i", "u", "small",
  "hr", "br",
  // SVG, because an icon in a mockup is an inline path and nothing else.
  "svg", "g", "path", "circle", "ellipse", "rect", "line", "polyline", "polygon", "text",
  "tspan", "defs", "use", "clippath", "mask", "lineargradient", "radialgradient", "stop",
]);

/**
 * What an authored `id` becomes in the rendered design.
 *
 * `id` used to be dropped while the stylesheet kept its `#foo` selectors, which is the worst
 * possible pair: `#auth { display: flex }` survived the sanitiser and then matched nothing,
 * so a two-column screen rendered as one column with no warning anywhere. The sheet said the
 * layout existed and the DOM had removed the hook it hung on.
 *
 * Keeping the id verbatim would be the obvious fix and is the wrong one: the design renders in
 * the app's own document, so an `id="root"` in a mockup would start matching the app's `#root`
 * rules. Prefixing costs nothing, cannot collide, and keeps the design's own selectors working.
 */
const ID_PREFIX = "k-";

/** An authored id we are willing to carry. Narrow, because it becomes a real DOM id. */
const SAFE_ID = /^[A-Za-z][\w-]{0,60}$/;

/** Attributes worth keeping. `data-k*` is handled by rule; everything else is named. */
const ALLOWED_ATTRS = new Set([
  "class", "style", "src", "href", "alt", "title", "type", "value", "placeholder", "role",
  "checked", "disabled", "readonly", "colspan", "rowspan", "width", "height",
  // SVG geometry.
  "viewbox", "d", "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin",
  "cx", "cy", "r", "rx", "ry", "x", "y", "x1", "y1", "x2", "y2", "points", "transform",
  "offset", "stop-color", "stop-opacity", "gradientunits", "xmlns", "fill-rule", "fill-opacity",
  "preserveaspectratio", "font-size", "text-anchor", "opacity",
]);

/** Properties a design is made of. A property not named here does not reach the DOM. */
const ALLOWED_CSS = new Set([
  "display", "flex", "flex-direction", "flex-wrap", "flex-grow", "flex-shrink", "flex-basis",
  "gap", "row-gap", "column-gap", "align-items", "align-self", "align-content",
  "justify-content", "justify-items", "justify-self", "order", "grid-template-columns",
  "grid-template-rows", "grid-column", "grid-row", "grid-area", "place-items", "place-content",
  "width", "height", "min-width", "min-height", "max-width", "max-height", "aspect-ratio",
  "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
  "padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
  "color", "background", "background-color", "background-image", "background-size",
  "background-position", "background-repeat", "background-clip",
  "font", "font-family", "font-size", "font-weight", "font-style", "font-variant",
  "line-height", "letter-spacing", "word-spacing", "text-align", "text-transform",
  "text-decoration", "text-overflow", "text-shadow", "white-space", "word-break", "overflow-wrap",
  "border", "border-top", "border-right", "border-bottom", "border-left", "border-width",
  "border-style", "border-color", "border-radius", "border-top-left-radius",
  "border-top-right-radius", "border-bottom-right-radius", "border-bottom-left-radius",
  "box-shadow", "box-sizing", "opacity", "overflow", "overflow-x", "overflow-y", "visibility",
  "position", "top", "right", "bottom", "left", "z-index", "inset",
  "transform", "transform-origin", "transition", "animation", "cursor", "list-style",
  "object-fit", "object-position", "fill", "stroke", "stroke-width", "backdrop-filter", "filter",
  /* Presentational, and absent for no reason but absence. Each renders, none of them can reach
   * outside the document, and between them they are where most of a design's visual range
   * lives — the difference between a mockup that looks drawn and one that looks typed. */
  "content", "mix-blend-mode", "isolation", "clip-path", "mask", "mask-image", "shape-outside",
  "outline", "outline-offset", "outline-color", "outline-width", "outline-style",
  "text-underline-offset", "text-decoration-color", "text-decoration-thickness", "text-wrap",
  "font-feature-settings", "font-variant-numeric", "font-stretch", "text-indent", "hyphens",
  "grid-auto-flow", "grid-auto-columns", "grid-auto-rows", "grid-template-areas", "gap-x", "gap-y",
  "will-change", "contain", "container-type", "scroll-behavior", "resize", "user-select",
  "caret-color", "accent-color", "appearance", "vertical-align", "float", "clear", "writing-mode",
  "direction", "unicode-bidi", "table-layout", "border-collapse", "border-spacing", "counter-reset",
  "counter-increment", "list-style-type", "list-style-position", "perspective", "transform-style",
  "backface-visibility", "rotate", "scale", "translate", "inset-inline", "inset-block",
  "margin-inline", "margin-block", "padding-inline", "padding-block", "border-start-start-radius",
  "border-start-end-radius", "border-end-start-radius", "border-end-end-radius",
]);

/**
 * The functions a value may contain.
 *
 * Closed rather than empty: `calc` is arithmetic, `var` is the whole point of the token dialect,
 * and a gradient is a fill. `url` is absent because a design that fetches is the thing the seal
 * existed to prevent, and `expression` and `attr` because they compute.
 */
const ALLOWED_FUNCS = new Set([
  "var", "calc", "min", "max", "clamp", "rgb", "rgba", "hsl", "hsla", "hwb", "oklch", "oklab",
  "color-mix", "linear-gradient", "radial-gradient", "conic-gradient", "repeating-linear-gradient",
  "translate", "translatex", "translatey", "translate3d", "scale", "scalex", "scaley", "rotate",
  "skew", "skewx", "skewy", "matrix", "perspective", "cubic-bezier", "steps", "blur",
  "drop-shadow", "brightness", "contrast", "saturate", "grayscale", "opacity", "env",
  // Grid's own arithmetic. `display: grid` was allowed and these were not, so every real grid
  // — which is to say every one with a `repeat()` in it — lost its template and collapsed to a
  // single column. Allowed as a layout or not at all; half of it was the worst of both.
  "repeat", "minmax", "fit-content", "counter", "attr-safe",
  // Type and motion.
  "cubic-bezier", "linear", "ease", "path", "circle", "ellipse", "inset", "polygon",
]);

/** Pseudo-classes a mockup legitimately uses. `::` and `:has()` are not among them. */
const ALLOWED_PSEUDO = new Set([
  "hover", "focus", "focus-visible", "active", "disabled", "checked",
  "first-child", "last-child", "only-child", "first-of-type", "last-of-type",
  "nth-child", "nth-of-type", "not", "is", "where", "empty", "placeholder",
]);

/* Pseudo-*elements*, which are a different thing and were refused wholesale by a `::` check.
 * A rule with no `::before` cannot draw a divider, a badge dot, a quote mark or an overlay
 * scrim — most of the vocabulary of decoration — and `content` was not a property either, so
 * the capability was closed from both ends at once. These four generate a box and nothing else;
 * none of them can fetch, and their content goes through the same value check as every other
 * declaration. */
const ALLOWED_PSEUDO_ELEMENT = new Set(["before", "after", "marker", "selection", "placeholder"]);

/** Nothing in a value should need either, and both are how a tokeniser is lied to. */
const OBFUSCATED = /\\|\/\*|\*\//;

/**
 * Control characters and whitespace, which is how `javascript:` is spelled when it is hiding.
 *
 * U+FFFD is in the list because of what the HTML parser does before we ever see the attribute: a
 * NUL in markup is replaced by the replacement character, so `java\0script:` arrives here as
 * `java�script:` — which no longer matches a control-character class and would sail through
 * a check written against the bytes that were sent.
 */
const INVISIBLE = /[ - - �]/g;

// ── values ──────────────────────────────────────────────────────────────────────────────────

/** Is this value made entirely of things that cannot reach outside the document? */
function safeValue(value: string): boolean {
  if (!value || OBFUSCATED.test(value)) return false;
  if (/[<>@;{}]/.test(value)) return false;
  for (const [, name] of value.matchAll(/([\w-]+)\s*\(/g)) {
    if (!ALLOWED_FUNCS.has(name.toLowerCase())) return false;
  }
  return true;
}

/**
 * One `prop: value` pair, or nothing.
 *
 * Exported because `edit.ts` writes declarations back into the file and has to refuse exactly
 * what this refuses. An inspector that could write a property the parser drops would put
 * something on disk the editor will never show again — and, worse, something a renderer with a
 * different whitelist might honour.
 */
export function safeDeclaration(prop: string, raw: string): [string, string] | null {
  const written = prop.trim();

  /* A document may define its own tokens.
   *
   * This one line is what makes "any design system" possible. Without it a design could
   * *reference* a token and never declare one, so the only palette that existed anywhere was the
   * nine the app injects — and the only legal escape was a literal hex, which the skill forbids
   * and the checker fails. The constraint that was supposed to buy consistency bought uniformity.
   *
   * Case is preserved, and that is not a detail: custom properties are case-sensitive while
   * every other property here is folded to lower case. Folding one would store `--brandAccent`
   * as `--brandaccent` while every `var(--brandAccent)` kept its case and resolved to nothing —
   * a colour that silently becomes the initial value, with no error and nothing to see.
   *
   * Two namespaces are refused. `--kith-*` is what the app itself is drawn in and what the
   * editor draws its selection outline and drop indicators with, inherited into the design
   * subtree; `--ds-*` is the resolved system. A document free to redefine either could switch
   * the editor's own affordances off inside the thing being edited, or quietly replace the
   * system it claims to be using. Neither is a security hole — both are a design lying about
   * itself.
   *
   * The value still goes through `safeValue`, so a token is as inert as any other declaration:
   * no `url()`, no escapes, no comments. A custom property is a *deferred* value, substituted
   * wherever `var()` appears — but substitution is token-level and the result is never
   * re-tokenised, so two halves of a blocked function cannot be reassembled across two tokens. */
  if (written.startsWith("--")) {
    if (!/^--[A-Za-z0-9_-]{1,60}$/.test(written)) return null;
    if (/^--(kith|ds)-/i.test(written)) return null;
    const value = raw.replace(/!\s*important/gi, "").trim();
    return safeValue(value) ? [written, value] : null;
  }

  const name = written.toLowerCase();
  if (!ALLOWED_CSS.has(name)) return null;
  // `!important` would outrank every inline edit the inspector makes, which would look like the
  // editor being broken rather than the design being emphatic.
  const value = raw.replace(/!\s*important/gi, "").trim();
  if (!safeValue(value)) return null;
  return [name, value];
}

/** Split on the semicolons that actually separate declarations. */
export function splitDeclarations(text: string): string[] {
  const out: string[] = [];
  let depth = 0;
  let quote = "";
  let start = 0;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quote) {
      if (c === quote) quote = "";
    } else if (c === '"' || c === "'") quote = c;
    else if (c === "(") depth++;
    else if (c === ")") depth = Math.max(0, depth - 1);
    else if (c === ";" && depth === 0) {
      out.push(text.slice(start, i));
      start = i + 1;
    }
  }
  out.push(text.slice(start));
  return out.filter((one) => one.trim());
}

/** An inline `style` attribute, whitelisted. */
function inlineStyle(text: string, dropped: string[]): Record<string, string> {
  const style: Record<string, string> = {};
  for (const one of splitDeclarations(text)) {
    const colon = one.indexOf(":");
    if (colon < 0) continue;
    const kept = safeDeclaration(one.slice(0, colon), one.slice(colon + 1));
    if (kept) style[kept[0]] = kept[1];
    else dropped.push(`style declaration ${one.trim().slice(0, 40)}`);
  }
  return style;
}

// ── urls ────────────────────────────────────────────────────────────────────────────────────

/** Images that are their own bytes. Not `svg+xml`, which is a document, not a picture. */
const DATA_IMAGE = /^data:image\/(png|jpeg|jpg|gif|webp|avif);/i;

/**
 * A reference that cannot leave the machine or climb out of the workspace.
 *
 * Whitespace and control characters come out before the scheme is read, because `java\tscript:`
 * and `java\0script:` are both `javascript:` by the time a browser navigates them.
 */
function safeUrl(raw: string): string | null {
  const bare = raw.replace(INVISIBLE, "");
  if (!bare) return null;
  if (DATA_IMAGE.test(bare)) return raw.trim();
  if (/^[a-z][a-z0-9+.-]*:/i.test(bare)) return null;  // any scheme at all
  if (bare.startsWith("//") || bare.startsWith("/")) return null;
  if (bare.includes("\\")) return null;
  if (bare.split("/").includes("..")) return null;
  return raw.trim();
}

// ── the stylesheet ──────────────────────────────────────────────────────────────────────────

/**
 * A selector naming the document itself: `html`, `body`, `:root`, or those chained.
 *
 * Rewritten to `:scope` — the design root — rather than refused. The refusal was right about
 * scoping and wrong about frequency: `body { --ink: #18322E; display: flex; width: 390px }` is
 * where anybody writing a whole-document design puts the tokens and the page box, and dropping
 * the rule dropped every custom property in it. Every colour in the design resolved to nothing,
 * the text took the app's own dark-mode foreground, and a clinic screen whose checker shot was
 * perfect arrived on the board as pale beige on white.
 *
 * Kept as a *rule* rather than lifted onto the root's inline style, because inline wins
 * everything: `@media (max-width: 600px) { body { padding: 12px } }` would lose to the base rule
 * it exists to override, which is the cascade a real browser runs and the checker sees.
 * `scopeStyleSheet` turns `:scope` into the root's own class.
 */
// One of the document's names, ending at a space, a `>` or the end — so `body.dark` (a compound
// on the document, refused) and a class called `.bodycopy` are not mistaken for it.
const DOCUMENT = /^(?:html|body|:root)(?=$|[\s>])\s*/i;

function documentSelector(selector: string): string | null {
  // The original spelling, not a lowercased copy: class names are case-sensitive.
  let rest = selector.trim();
  let named = false;
  let child = false;
  for (let head = rest.match(DOCUMENT); head; head = rest.match(DOCUMENT)) {
    named = true;
    child = false;
    rest = rest.slice(head[0].length);
    if (rest.startsWith(">")) {
      child = true;
      rest = rest.slice(1).trim();
    }
  }
  if (!named) return null;
  if (!rest) return child ? null : ":scope";
  // `body > .app` is a child of the root; `body .card` is anything inside it, which every
  // scoped selector already is. Siblings of the document are not a thing.
  if (/^[+~]/.test(rest) || !safeSelector(rest)) return null;
  return child ? `:scope > ${rest}` : rest;
}

/**
 * A selector that stays inside the design when the sheet is scoped to its root.
 *
 * `html`, `body` and `:root` are refused *here*: scoping prepends an ancestor, and a selector
 * naming an ancestor of that ancestor either matches nothing (confusing) or, if the scoping is
 * ever done differently, matches the app (dangerous). `documentSelector` above is the one place
 * they are let back in, rewritten to name the root. `*` is refused because a universal rule
 * under a scope is still a rule over every node in the design, which is not something he means
 * often enough to be worth allowing.
 */
function safeSelector(selector: string): boolean {
  const one = selector.trim().toLowerCase();
  if (!one || one.length > 200) return false;
  if (OBFUSCATED.test(one) || /[{}@]/.test(one)) return false;
  if (/(^|[\s>+~,])(html|body|\*)([\s>+~,.:#[]|$)/.test(one)) return false;
  if (one.includes(":root") || one.includes("[")) return false;

  /* Pseudo-elements are checked as themselves rather than banned by the sight of a colon.
   *
   * The `::` rejection took out `::before` and `::after` along with everything else, which
   * closed off most of the vocabulary of decoration — a divider, a badge dot, an overlay scrim —
   * for no reason beyond the syntax being two characters instead of one. Both lists are closed,
   * so a construct nobody named is still refused. */
  const body = one.replace(/::([\w-]+)/g, (_, name: string) =>
    ALLOWED_PSEUDO_ELEMENT.has(name) ? "" : "::?",
  );
  if (body.includes("::?")) return false;

  for (const [, name] of body.matchAll(/:([\w-]+)/g)) {
    if (!ALLOWED_PSEUDO.has(name)) return false;
  }
  return /^[\w\s.,#:()>+~-]+$/.test(body);
}

/** The declarations of one rule, rebuilt. */
function ruleBody(body: string, dropped: string[]): string {
  const kept: string[] = [];
  for (const one of splitDeclarations(body)) {
    const colon = one.indexOf(":");
    if (colon < 0) continue;
    const pair = safeDeclaration(one.slice(0, colon), one.slice(colon + 1));
    if (pair) kept.push(`${pair[0]}: ${pair[1]}`);
    else dropped.push(`stylesheet declaration ${one.trim().slice(0, 40)}`);
  }
  return kept.join("; ");
}

/**
 * His stylesheet, rebuilt from the parts of it that are inert.
 *
 * Hand-scanned rather than handed to the CSSOM, because what the CSSOM gives back differs
 * between a browser and jsdom in exactly the places that matter here — an at-rule it does not
 * know is dropped by one and preserved as text by the other, and a boundary whose behaviour
 * depends on the environment it is tested in is not a boundary.
 */
/** The inside of a keyframe block: `from`, `to`, and percentages, each holding declarations. */
function sanitiseKeyframes(body: string, dropped: string[]): string {
  const out: string[] = [];
  for (const match of body.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const stop = match[1].trim().toLowerCase();
    if (!/^(from|to|\d{1,3}%)(\s*,\s*(from|to|\d{1,3}%))*$/.test(stop)) {
      dropped.push(`keyframe ${stop.slice(0, 20)}`);
      continue;
    }
    const declarations = ruleBody(match[2], dropped);
    if (declarations) out.push(`${stop} { ${declarations} }`);
  }
  return out.join("\n");
}

export function sanitiseStyleSheet(css: string, dropped: string[]): string {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const out: string[] = [];
  let i = 0;

  while (i < text.length) {
    const open = text.indexOf("{", i);
    const semicolon = text.indexOf(";", i);

    // An at-rule that is a statement rather than a block — `@import`, `@charset`, `@namespace` —
    // ends at its semicolon. Finding the next `{` first would make the whole of it, *plus* the
    // selector of the innocent rule after it, into one prelude: the import would be dropped and
    // so would the rule that followed it, which is how a sanitiser quietly eats a stylesheet.
    if (semicolon >= 0 && (open < 0 || semicolon < open)) {
      const statement = text.slice(i, semicolon).trim();
      if (statement.startsWith("@")) dropped.push(`at-rule ${statement.slice(0, 40)}`);
      i = semicolon + 1;
      continue;
    }

    if (open < 0) break;
    const prelude = text.slice(i, open).trim();

    // Find this block's close, counting nesting so an @media body comes out whole.
    let depth = 1;
    let j = open + 1;
    for (; j < text.length && depth > 0; j++) {
      if (text[j] === "{") depth++;
      else if (text[j] === "}") depth--;
    }
    const body = text.slice(open + 1, j - 1);
    i = j;

    if (prelude.startsWith("@")) {
      // A media query renders even though nothing in it can be dragged; every other at-rule
      // either fetches (`@import`, `@font-face`) or declares something the editor cannot see.
      if (/^@media\b/i.test(prelude) && safeValue(prelude.slice(6).replace(/[()]/g, " "))) {
        const inner = sanitiseStyleSheet(body, dropped);
        if (inner) out.push(`${prelude} {\n${inner}\n}`);
      } else if (/^@(keyframes|supports)\b/i.test(prelude) && /^@[\w-]+\s+[\w\s(),:.%<>=-]{0,120}$/.test(prelude)) {
        /* `animation` was on the property list and `@keyframes` was not, so every animation a
         * design carried was wired to a name that did not exist — it rendered still and nothing
         * said why. A keyframe block's contents are declarations and go through the same
         * whitelist as any other; its percentage selectors are not selectors in the sense
         * `safeSelector` means, so they are matched here instead. */
        const inner = sanitiseKeyframes(body, dropped);
        if (inner) out.push(`${prelude} {\n${inner}\n}`);
      } else {
        dropped.push(`at-rule ${prelude.slice(0, 40)}`);
      }
      continue;
    }

    const selectors = prelude.split(",").map((one) => one.trim()).filter(Boolean);
    const safe: string[] = [];
    for (const one of selectors) {
      const kept = documentSelector(one) ?? (safeSelector(one) ? one : null);
      if (kept) safe.push(kept);
      else dropped.push(`selector ${one.slice(0, 40)}`);
    }
    if (!safe.length) continue;

    const declarations = ruleBody(body, dropped);
    // The same prefix the tree applies to `id`. Done here rather than at the call site so a
    // selector and the attribute it targets can never be rewritten by different rules.
    const scoped = safe.map((one) => one.replace(/#([A-Za-z][\w-]*)/g, `#${ID_PREFIX}$1`));
    if (declarations) out.push(`${scoped.join(", ")} { ${declarations} }`);
  }

  return out.join("\n");
}

// ── the tree ────────────────────────────────────────────────────────────────────────────────

/**
 * The id of the tree's top, which is not a node in the file.
 *
 * A design is a list of top-level elements, not one element, so the tree needs a holder for them
 * and the holder needs a name the editor can pass around. It is deliberately not a legal minted
 * or authored id, so nothing in a document can collide with it.
 */
export const ROOT_ID = "root";

/** Names an id can take without needing quoting anywhere downstream. */
const ID = /^[\w.:-]{1,60}$/;

/**
 * HTML's collapsible whitespace, which is not JavaScript's `\s`: that also matches the
 * non-breaking space, so `Chart ready&nbsp; ✓` lost the one space the author insisted on.
 */
const COLLAPSIBLE = /[ \t\n\r\f]+/g;
const EDGES = /^[ \t\n\r\f]+|[ \t\n\r\f]+$/g;

/** The text a node owns, as opposed to the text its children own. */
function ownText(element: Element): string {
  let text = "";
  for (const node of Array.from(element.childNodes)) {
    if (node.nodeType === 3) text += node.nodeValue ?? "";
  }
  return text.replace(COLLAPSIBLE, " ").replace(EDGES, "");
}

function convert(element: Element, mint: () => string, taken: Set<string>, dropped: string[]): DesignNode | DesignNode[] {
  const tag = element.tagName.toLowerCase();

  if (DROP_SUBTREE.has(tag)) {
    dropped.push(`${tag} tag`);
    return [];
  }

  /*
   * An unknown tag becomes a `div`. It used to be spliced away, and that was the same mistake
   * as dropping `id` while keeping `#id` selectors: it removed the element the layout hung on.
   *
   * The case that found it is the ordinary one. `form` is not on the list, and a form is where
   * anybody puts the column: `<form style="display:flex; flex-direction:column; gap:24px">`.
   * Splicing its children into the parent — a centring `display:flex` with no direction —
   * turned a stacked sign-in form into a single horizontal band of labels and fields, on every
   * auth screen ever drawn here, with nothing in the warning strip but "form tag".
   *
   * Renaming keeps the box, its whitelisted style and its `data-k`, and renders something
   * inert. That is better than adding `form` to the list, because a real `<form>` in the app's
   * own document submits on Enter; a div looks identical and cannot.
   */
  const unknown = !ALLOWED_TAGS.has(tag);
  if (unknown) dropped.push(`${tag} tag`);

  const attrs: Record<string, string> = {};
  let style: Record<string, string> = {};
  let classes: string[] = [];
  let absolute = false;
  let component = "";
  let authored = "";

  for (const attribute of Array.from(element.attributes)) {
    const name = attribute.name.toLowerCase();
    const value = attribute.value;

    if (name === "data-k") {
      authored = ID.test(value) ? value : "";
      continue;
    }
    if (name === "data-k-absolute") {
      absolute = true;
      continue;
    }
    if (name === "id") {
      // Prefixed rather than kept or dropped — see ID_PREFIX. The sheet is rewritten to match
      // in `sanitiseStyleSheet`, so the two halves cannot come apart again.
      if (SAFE_ID.test(value)) attrs.id = ID_PREFIX + value;
      else dropped.push(`id ${value.slice(0, 20)}`);
      continue;
    }
    if (name === "data-k-component") {
      // Same shape the service and the checker accept, so a part named one way in the system
      // is the same part here. See `_components` in kith/services/design_system.py.
      component = /^[\w.-]{1,60}$/.test(value) ? value : "";
      continue;
    }
    if (name.startsWith("on")) {
      dropped.push(`${name} attribute`);
      continue;
    }
    if (name === "style") {
      style = inlineStyle(value, dropped);
      continue;
    }
    if (name === "class") {
      classes = value.split(/\s+/).filter(Boolean);
      continue;
    }
    if (name.startsWith("aria-")) {
      attrs[name] = value;
      continue;
    }
    if (!ALLOWED_ATTRS.has(name)) {
      dropped.push(`${name} attribute`);
      continue;
    }
    if (name === "src" || name === "href") {
      const url = safeUrl(value);
      if (url === null) dropped.push(`${name} ${value.slice(0, 40)}`);
      else attrs[name] = url;
      continue;
    }
    attrs[name] = value;
  }

  let id = authored;
  if (!id || taken.has(id)) {
    if (id) {
      // Two nodes answering to one patch is worse than a renamed node: the patch would land on
      // whichever the walk reached first, which is not a thing anyone could predict.
      let n = 2;
      while (taken.has(`${id}-${n}`)) n++;
      id = `${id}-${n}`;
    } else {
      id = mint();
    }
  }
  taken.add(id);

  /* Children after the id, not before.
   *
   * `convert` used to walk its children first and mint its own id afterwards, which numbered a
   * document depth-first *post*-order: the deepest node became k1 and the outermost frame got the
   * highest number. Nothing rendered differently, so it was invisible — until `edit.ts`, which
   * writes ids into the source text by scanning open tags in the order they appear. Source order
   * is pre-order. Any minted id past the first nesting would have been written onto a different
   * node than the one the editor thought it had selected, which is a patch landing silently on
   * the wrong element. */
  const children: DesignNode[] = [];
  const order: Array<string | number> = [];
  for (const child of Array.from(element.childNodes)) {
    if (child.nodeType === 3) {
      order.push((child.nodeValue ?? "").replace(COLLAPSIBLE, " "));
    } else if (child.nodeType === 1) {
      for (const one of [convert(child as Element, mint, taken, dropped)].flat()) {
        order.push(children.length);
        children.push(one);
      }
    }
  }

  const text = ownText(element);
  // Only for mixed content. A node of pure children keeps rendering exactly as it did — the
  // indentation between block children is not text anyone wrote.
  let segments: Array<string | number> | undefined;
  if (text && children.length) {
    segments = order.filter((one) => one !== "");
    const first = segments[0];
    const last = segments[segments.length - 1];
    if (typeof first === "string") segments[0] = first.replace(/^[ \t\n\r\f]+/, "");
    if (typeof last === "string") segments[segments.length - 1] = last.replace(/[ \t\n\r\f]+$/, "");
    segments = segments.filter((one) => one !== "");
  }
  return {
    id,
    tag: unknown ? "div" : tag,
    attrs,
    style,
    classes,
    ...(text ? { text } : {}),
    ...(segments ? { segments } : {}),
    children,
    absolute,
    component,
  };
}

/**
 * His design, as a tree with handles on it.
 *
 * Ids are minted in document order rather than randomly, so a patch written against one parse
 * still addresses the same node in the next one — an editor whose ids changed on every reload
 * could not save anything.
 */
export function parseDesign(html: string): ParsedDesign {
  const dropped: string[] = [];
  const document = new DOMParser().parseFromString(html || "", "text/html");

  const sheets = Array.from(document.querySelectorAll("style"));
  const sheet = sheets.map((one) => sanitiseStyleSheet(one.textContent ?? "", dropped)).filter(Boolean).join("\n");
  for (const one of sheets) one.remove();

  const taken = new Set<string>();
  let next = 0;
  const mint = () => {
    let id = `k${++next}`;
    while (taken.has(id)) id = `k${++next}`;
    return id;
  };

  const body = document.body ?? document.createElement("body");
  const children = Array.from(body.children).flatMap((child) => convert(child, mint, taken, dropped));

  /*
   * `<body style="...">` is the design root's style, and it was being thrown away.
   *
   * The dialect tells him a design may declare the values the system has not named — `<div
   * data-k="page" style="--brand-shadow: ...">` — and a whole-document design puts exactly that
   * on `<body>`, which is where anyone writing HTML would put it. The parse started at
   * `body.children`, so every one of those custom properties was undefined by the time the tree
   * rendered, and `background: var(--mk-bg)` resolved to nothing: a dark screen arrived as a
   * light one with no colour in it and nothing in the warning strip to say why.
   *
   * The declarations go through the same whitelist as any other inline style, so this widens
   * what is *read*, not what is allowed.
   */
  const rootStyle = inlineStyle(body.getAttribute("style") ?? "", dropped);

  // On `<body>` first, because that is the design as a whole. On the outermost element as a
  // fallback, because a fragment written without a document still has one frame.
  const frame = readFrame(
    body.getAttribute("data-k-frame") ?? body.firstElementChild?.getAttribute("data-k-frame"),
  );

  return {
    frame,
    root: { id: ROOT_ID, tag: "root", attrs: {}, style: rootStyle, classes: [], children, absolute: false, component: "" },
    sheet,
    dropped,
  };
}
