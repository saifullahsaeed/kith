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
  children: DesignNode[];
  /** Opted into free positioning with `data-k-absolute`, so drag means x/y here. */
  absolute: boolean;
}

export interface ParsedDesign {
  root: DesignNode;
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
]);

/** Pseudo-classes a mockup legitimately uses. `::` and `:has()` are not among them. */
const ALLOWED_PSEUDO = new Set([
  "hover", "focus", "focus-visible", "active", "disabled", "checked",
  "first-child", "last-child", "only-child", "nth-child", "not",
]);

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
  const name = prop.trim().toLowerCase();
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
 * A selector that stays inside the design when the sheet is scoped to its root.
 *
 * `html`, `body` and `:root` are refused rather than rewritten: scoping prepends an ancestor, and
 * a selector naming an ancestor of that ancestor either matches nothing (confusing) or, if the
 * scoping is ever done differently, matches the app (dangerous). `*` is refused because a
 * universal rule under a scope is still a rule over every node in the design, which is not
 * something he means often enough to be worth allowing.
 */
function safeSelector(selector: string): boolean {
  const one = selector.trim().toLowerCase();
  if (!one || one.length > 200) return false;
  if (OBFUSCATED.test(one) || /[{}@]/.test(one)) return false;
  if (/(^|[\s>+~,])(html|body|\*)([\s>+~,.:#[]|$)/.test(one)) return false;
  if (one.includes(":root") || one.includes("::") || one.includes("[")) return false;
  for (const [, name] of one.matchAll(/:([\w-]+)/g)) {
    if (!ALLOWED_PSEUDO.has(name)) return false;
  }
  return /^[\w\s.,#:()>+~-]+$/.test(one);
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
      } else {
        dropped.push(`at-rule ${prelude.slice(0, 40)}`);
      }
      continue;
    }

    const selectors = prelude.split(",").map((one) => one.trim()).filter(Boolean);
    const safe = selectors.filter(safeSelector);
    for (const refused of selectors.filter((one) => !safe.includes(one))) {
      dropped.push(`selector ${refused.slice(0, 40)}`);
    }
    if (!safe.length) continue;

    const declarations = ruleBody(body, dropped);
    if (declarations) out.push(`${safe.join(", ")} { ${declarations} }`);
  }

  return out.join("\n");
}

// ── the tree ────────────────────────────────────────────────────────────────────────────────

/** Names an id can take without needing quoting anywhere downstream. */
const ID = /^[\w.:-]{1,60}$/;

/** The text a node owns, as opposed to the text its children own. */
function ownText(element: Element): string {
  let text = "";
  for (const node of Array.from(element.childNodes)) {
    if (node.nodeType === 3) text += node.nodeValue ?? "";
  }
  return text.replace(/\s+/g, " ").trim();
}

function convert(element: Element, mint: () => string, taken: Set<string>, dropped: string[]): DesignNode | DesignNode[] {
  const tag = element.tagName.toLowerCase();

  if (DROP_SUBTREE.has(tag)) {
    dropped.push(`${tag} tag`);
    return [];
  }

  if (!ALLOWED_TAGS.has(tag)) {
    // Unwrapped, not deleted. See DROP_SUBTREE for why these two are different.
    dropped.push(`${tag} tag`);
    return Array.from(element.children).flatMap((child) => convert(child, mint, taken, dropped));
  }

  const attrs: Record<string, string> = {};
  let style: Record<string, string> = {};
  let classes: string[] = [];
  let absolute = false;
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
  const children = Array.from(element.children).flatMap((child) => convert(child, mint, taken, dropped));

  const text = ownText(element);
  return {
    id,
    tag,
    attrs,
    style,
    classes,
    ...(text ? { text } : {}),
    children,
    absolute,
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

  return {
    root: { id: "root", tag: "root", attrs: {}, style: {}, classes: [], children, absolute: false },
    sheet,
    dropped,
  };
}
