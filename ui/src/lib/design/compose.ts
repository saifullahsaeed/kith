/**
 * Screens made of parts that are referenced, not pasted.
 *
 * ── The failure this exists for ─────────────────────────────────────────────────────────────
 *
 * The inventory used to be snippets. `read_design_system` handed him a button's markup and he
 * pasted it into each screen with a `data-k-component` label on the copy, so the label named a
 * part and the bytes were whatever that screen's copy had drifted to. Measured 2026-10-01 across
 * his real projects: ironline's `button.primary` was used 7 times in 4 different versions,
 * sadeef-v2's `notification.row` 6 times in 4. On the one project big enough to need reuse —
 * 54 screens for Pace — he gave up on the format and wrote a Python templating engine
 * (`.kith/design/pace.py`, `build-all.py`). That made the screens consistent and broke the
 * editor: every hand edit was overwritten the next time the generator ran, and the nine
 * components in `system.json` were a second source of truth nobody read.
 *
 * So a part is now a file — `.kith/design/components/<name>.html`, beside the system — and a
 * screen says `<k-use name="row.detail" label="Checking">`. Change the file and every screen
 * that uses it changes; that is the whole mechanism, and it is the same one Claude Design's
 * `<dc-import>` and Figma's main components are built on.
 *
 * ── Declarative only, on purpose ────────────────────────────────────────────────────────────
 *
 * Claude Design's format also runs a script class per artboard. That half cannot come here:
 * designs render in the app's own document behind the whitelist in `parse.ts`, and a design
 * that computed would be a design that could do anything. So a hole is a *lookup* —
 * `{{label}}` — never an expression, and the value it is filled with is escaped before the
 * markup is parsed, so a prop can carry text and nothing else. Whatever comes out of filling
 * still goes through the same whitelist as anything he writes by hand.
 *
 * ── Two expansions, one answer ──────────────────────────────────────────────────────────────
 *
 * The renderer expands `<k-use>` while it builds the tree (`parse.ts`), because it has to know
 * which node came from which file. Two other places want the plain markup instead: *detaching*
 * an instance, which writes the part's markup into the screen, and the standalone checker,
 * which renders in a bare Chromium. `expandText` is that text-level expansion and
 * `check_design.py` carries the same one in Python; `expand.fixture.json` holds both to the
 * same answers.
 */

/** A part as the renderer needs it: its markup, and the file it lives in when it has one. */
export interface Part {
  html: string;
  path?: string;
}

export type Parts = Record<string, Part>;

/** The two tags this dialect adds. Both are structural: neither ever reaches the DOM. */
export const USE_TAG = "k-use";
export const SLOT_TAG = "k-slot";

/** How deep parts may nest. A real product goes two or three; past this it is a cycle. */
export const MAX_DEPTH = 8;

/** Attributes on a `<k-use>` that are not props — they say which part, and how this use differs. */
const RESERVED = new Set(["name", "style", "class", "data-k"]);

/** A prop or hole name. Lowercase, because the HTML parser lowercases attribute names. */
const PROP = /^[a-z_][\w.-]{0,60}$/;

/** `{{ label }}`, `{{account.name}}`. Nothing else is a hole, so `{{ 1 + 1 }}` stays as text. */
const HOLE = /\{\{\s*([A-Za-z_][\w.-]{0,60})\s*\}\}/g;

/** `<!-- use: one line -->`, the first thing in a part's file. */
const USE_NOTE = /^\s*<!--\s*use:\s*([\s\S]*?)-->/i;

/**
 * `<k-use … />` written as an open and a close.
 *
 * The HTML parser does not honour a self-closing slash on an element it does not know, so
 * `<k-use name="a" /><k-use name="b" />` parses as the second *inside* the first — and every
 * sibling after it too. He will write it self-closed, because every component syntax he has
 * seen allows it, so it is normalised before anything parses it.
 */
export function openAndClose(html: string): string {
  return html.replace(
    /<(k-use|k-slot)\b((?:"[^"]*"|'[^']*'|[^>"'])*?)\s*\/>/gi,
    (_, tag: string, attrs: string) => `<${tag}${attrs}></${tag}>`,
  );
}

/** HTML's own escaping, enough that a prop value can only ever be text in an attribute or a node. */
export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/**
 * A part's markup with its holes filled.
 *
 * A hole with no prop given becomes empty rather than staying `{{label}}`: a screen that forgot
 * one prop should look like a screen with a blank, not like a template. Props are matched case
 * blind because the HTML parser has already lowercased the attribute that carries them.
 */
export function fillHoles(html: string, props: Record<string, string>): string {
  const lower: Record<string, string> = {};
  for (const [name, value] of Object.entries(props)) lower[name.toLowerCase()] = value;
  return html.replace(HOLE, (_, name: string) => escapeHtml(lower[name.toLowerCase()] ?? ""));
}

/**
 * A part's holes filled for looking at, with the ones nobody gave a value left showing.
 *
 * Different from `fillHoles` on purpose: on a screen a missing prop should look like a blank,
 * but on the part's own page an unfilled `{{value}}` is information — it is where a screen's
 * value goes.
 */
export function previewHoles(html: string, props: Record<string, string>): string {
  const lower: Record<string, string> = {};
  for (const [name, value] of Object.entries(props)) lower[name.toLowerCase()] = value;
  return html.replace(HOLE, (whole, name: string) => {
    const value = lower[name.toLowerCase()];
    return value === undefined ? whole : escapeHtml(value);
  });
}

/** The holes a part declares, in the order it first uses them. */
export function holesOf(html: string): string[] {
  const out: string[] = [];
  for (const [, name] of html.matchAll(HOLE)) {
    const key = name.toLowerCase();
    if (!out.includes(key)) out.push(key);
  }
  return out;
}

/** The one-line note at the top of a part's file, and the markup without it. */
export function splitNote(html: string): { use: string; markup: string } {
  const match = USE_NOTE.exec(html);
  if (!match) return { use: "", markup: html.trim() };
  return { use: match[1].trim(), markup: html.slice(match[0].length).trim() };
}

/** The props an element carries: every attribute that is not one of the reserved four. */
export function propsOf(attributes: Iterable<{ name: string; value: string }>): Record<string, string> {
  const props: Record<string, string> = {};
  for (const { name, value } of attributes) {
    const key = name.toLowerCase();
    if (RESERVED.has(key) || key.startsWith("data-k") || !PROP.test(key)) continue;
    props[key] = value;
  }
  return props;
}

// ── text-level expansion ────────────────────────────────────────────────────────────────────

/** Comments and tags, quotes respected — the same scanner `edit.ts` uses. */
const TAG = /<!--[\s\S]*?-->|<(\/?)([a-zA-Z][\w:-]*)((?:"[^"]*"|'[^']*'|[^>"'])*)>/g;

/** One `name="value"` list, read the way the HTML parser reads it. */
export function readAttributes(text: string): { name: string; value: string }[] {
  const out: { name: string; value: string }[] = [];
  const attr = /([^\s"'=<>/]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;
  for (const match of text.matchAll(attr)) {
    const raw = match[2] ?? match[3] ?? match[4] ?? "";
    out.push({ name: match[1].toLowerCase(), value: decodeEntities(raw) });
  }
  return out;
}

/** The handful of entities an attribute value is actually written with. */
function decodeEntities(text: string): string {
  return text
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");
}

/** Where the first `<k-use>` in this text begins and ends, counting nested ones. */
function nextUse(html: string, from: number): { start: number; openEnd: number; closeStart: number; end: number; attrs: string } | null {
  const scan = new RegExp(TAG.source, "g");
  scan.lastIndex = from;
  let open: RegExpExecArray | null = null;
  let depth = 0;
  for (let match = scan.exec(html); match; match = scan.exec(html)) {
    if (match[0].startsWith("<!--")) continue;
    if (match[2].toLowerCase() !== USE_TAG) continue;
    const closing = match[1] === "/";
    if (!closing) {
      if (!open) open = match;
      depth++;
      continue;
    }
    depth--;
    if (open && depth === 0) {
      return {
        start: open.index,
        openEnd: open.index + open[0].length,
        closeStart: match.index,
        end: match.index + match[0].length,
        attrs: open[3] ?? "",
      };
    }
  }
  // An unclosed `<k-use>` runs to the end of the document, the way the parser reads it.
  if (open) {
    return { start: open.index, openEnd: open.index + open[0].length, closeStart: html.length, end: html.length, attrs: open[3] ?? "" };
  }
  return null;
}

/** Declarations from `extra` laid over `base`, later wins — the cascade an inline style runs. */
export function mergeStyle(base: string, extra: string): string {
  const order: string[] = [];
  const values = new Map<string, string>();
  for (const text of [base, extra]) {
    for (const one of text.split(";")) {
      const colon = one.indexOf(":");
      if (colon < 0) continue;
      const property = one.slice(0, colon).trim();
      const value = one.slice(colon + 1).trim();
      if (!property || !value) continue;
      const key = property.startsWith("--") ? property : property.toLowerCase();
      if (!values.has(key)) order.push(key);
      values.set(key, value);
    }
  }
  return order.map((key) => `${key}: ${values.get(key)}`).join("; ");
}

/**
 * The use's own `style` and `class` put onto the part's first element.
 *
 * This is what "this screen only" means for the outside of an instance: the part's padding
 * stays the part's, and the one screen that needs it tighter says so on its own `<k-use>`.
 */
function dressRoot(markup: string, style: string, classes: string, name: string): string {
  const scan = new RegExp(TAG.source, "g");
  for (let match = scan.exec(markup); match; match = scan.exec(markup)) {
    if (match[0].startsWith("<!--") || match[1] === "/") continue;
    const tag = match[2].toLowerCase();
    if (tag === "style") {
      // Skip past the sheet to the first element that is part of the layout.
      const close = markup.toLowerCase().indexOf("</style>", scan.lastIndex);
      if (close < 0) return markup;
      scan.lastIndex = close + 8;
      continue;
    }
    let attrs = match[3] ?? "";
    const selfClosed = attrs.trimEnd().endsWith("/");
    if (selfClosed) attrs = attrs.replace(/\s*\/\s*$/, "");
    const styleAt = /\s*\bstyle\s*=\s*(?:"([^"]*)"|'([^']*)')/i.exec(attrs);
    const classAt = /\s*\bclass\s*=\s*(?:"([^"]*)"|'([^']*)')/i.exec(attrs);
    let next = attrs;
    if (style.trim()) {
      const merged = mergeStyle(styleAt?.[1] ?? styleAt?.[2] ?? "", style);
      next = styleAt ? next.replace(styleAt[0], ` style="${merged}"`) : `${next} style="${merged}"`;
    }
    if (classes.trim()) {
      const joined = [classAt?.[1] ?? classAt?.[2] ?? "", classes].join(" ").trim();
      const current = /\s*\bclass\s*=\s*(?:"([^"]*)"|'([^']*)')/i.exec(next);
      next = current ? next.replace(current[0], ` class="${joined}"`) : `${next} class="${joined}"`;
    }
    if (!/\bdata-k-component\s*=/i.test(next)) next = `${next} data-k-component="${name}"`;
    const rebuilt = `<${match[2]}${next}${selfClosed ? " /" : ""}>`;
    return markup.slice(0, match.index) + rebuilt + markup.slice(match.index + match[0].length);
  }
  return markup;
}

/** What a missing part becomes in plain markup: visible, labelled, and holding what was put in it. */
export function missingMarkup(name: string, inner: string): string {
  return (
    `<div data-k-missing="${escapeHtml(name)}" style="border: 1px dashed #b42318; padding: 8px; ` +
    `color: #b42318; font: 12px system-ui, sans-serif">Missing component: ${escapeHtml(name)}${inner}</div>`
  );
}

export interface ExpandOptions {
  /** Expand parts used inside parts. Off for detaching, which takes one level off and no more. */
  deep?: boolean;
  /** Called on each part's filled markup before it is spliced in; detaching renames ids here. */
  rename?: (markup: string, attrs: { name: string; value: string }[]) => string;
}

/**
 * Every `<k-use>` in this markup replaced by the part it names.
 *
 * The order matters and is the order `parse.ts` builds the tree in: the slot content is
 * expanded first (it may hold uses of its own), then the part's holes are filled, then the slot
 * content goes where `<k-slot>` was, then the use's own style and class go on the part's first
 * element.
 */
export function expandText(html: string, parts: Parts, options: ExpandOptions = {}, stack: string[] = []): string {
  const deep = options.deep ?? true;
  const source = openAndClose(html);
  let out = "";
  let at = 0;
  for (let hit = nextUse(source, at); hit; hit = nextUse(source, at)) {
    out += source.slice(at, hit.start);
    at = hit.end;

    const attrs = readAttributes(hit.attrs);
    const name = attrs.find((one) => one.name === "name")?.value ?? "";
    const inner = expandText(source.slice(hit.openEnd, hit.closeStart), parts, options, stack);
    const part = parts[name];
    if (!part || stack.includes(name) || stack.length >= MAX_DEPTH) {
      out += missingMarkup(name || "(no name)", inner);
      continue;
    }

    let markup = fillHoles(splitNote(part.html).markup, propsOf(attrs));
    markup = openAndClose(markup).replace(/<k-slot\b[^>]*><\/k-slot>/gi, () => inner);
    markup = dressRoot(
      markup,
      attrs.find((one) => one.name === "style")?.value ?? "",
      attrs.find((one) => one.name === "class")?.value ?? "",
      name,
    );
    if (options.rename) markup = options.rename(markup, attrs);
    out += deep ? expandText(markup, parts, options, [...stack, name]) : markup;
  }
  return out + source.slice(at);
}
