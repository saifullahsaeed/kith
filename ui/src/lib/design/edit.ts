/**
 * Changing a design by changing the file, in the smallest way that says what you did.
 *
 * This is where the format argument is cashed in. HTML was chosen over a node tree because an
 * edit comes back to him as readable intent — `padding: 16px → 24px`, a sentence about a
 * decision — and that is only true if an edit touches the declaration it changed and nothing
 * else. Parse-and-reprint would be a tenth of this code and would produce, for one drag, a diff
 * in which every line of his file moved. He would read it as "the editor rewrote my design",
 * which is both what it looks like and what it is.
 *
 * So edits are splices into the source text, addressed by `data-k`, and the tests are mostly
 * about what did not move.
 *
 * ── Why ids have to be written down ─────────────────────────────────────────────────────────
 *
 * The parser mints an id for every node he did not name, so the editor can address all of them.
 * Those ids exist only in memory, and a patch that named one would be meaningless to the next
 * parse of a file that never received it. `materialiseIds` writes them in — once, on the first
 * edit — so from then on every handle in the editor corresponds to something in the document.
 *
 * It refuses to guess. The scan here walks open tags in source order and the parser mints in
 * document order, and those agree for everything a browser parses the obvious way. When the
 * counts differ — a stray `</div>`, a table with loose markup in it — nothing is written at all,
 * because the failure mode of proceeding is an id on the wrong node, and an edit that silently
 * lands somewhere else is worse than an edit that does not happen.
 */
import { expandText, type Parts, SLOT_TAG, USE_TAG } from "@/lib/design/compose";
import {
  ALLOWED_TAGS,
  DROP_SUBTREE,
  parseDesign,
  ROOT_ID,
  safeDeclaration,
  splitDeclarations,
} from "@/lib/design/parse";

/** One open tag in the source, with the span it occupies. */
interface TagHit {
  name: string;
  /** Everything between the tag name and the closing `>`, as written. */
  attrs: string;
  start: number;
  end: number;
}

/**
 * Comments, and tags with their attribute text — quotes respected so a `>` inside a value does
 * not end the tag early.
 */
const TAG = /<!--[\s\S]*?-->|<(\/?)([a-zA-Z][\w:-]*)((?:"[^"]*"|'[^']*'|[^>"'])*)>/g;

/** Tags whose contents are skipped wholesale, and which have a closing tag to skip to. */
const SKIP_UNTIL_CLOSED = new Set(["script", "noscript", "template", "title"]);

/**
 * The open tags the parser would have made nodes of, in the order it would have reached them.
 *
 * Not a parse. It deliberately does not build a tree, because the thing being matched is the
 * *sequence* the parser mints in, and a second tree would be a second opinion about nesting —
 * which is exactly the disagreement that would put an id on the wrong node.
 */
function scanTags(html: string): TagHit[] {
  const hits: TagHit[] = [];
  let skipping = "";

  for (const match of html.matchAll(TAG)) {
    if (match[0].startsWith("<!--")) continue;
    const closing = match[1] === "/";
    const name = match[2].toLowerCase();

    if (skipping) {
      if (closing && name === skipping) skipping = "";
      continue;
    }
    if (closing) continue;
    if (DROP_SUBTREE.has(name)) {
      if (SKIP_UNTIL_CLOSED.has(name)) skipping = name;
      continue;
    }
    // A use and a slot are nodes in the tree the parser mints for, so they are tags here too —
    // left out, every id after the first `<k-use>` would be written one node early.
    if (!ALLOWED_TAGS.has(name) && name !== USE_TAG && name !== SLOT_TAG) continue;

    hits.push({
      name,
      attrs: match[3] ?? "",
      start: match.index,
      end: match.index + match[0].length,
    });
  }

  return hits;
}

/** Every id the parser assigns, in the order it assigns them. */
function mintedOrder(html: string): string[] {
  const out: string[] = [];
  const walk = (node: { id: string; children: { id: string; children: unknown[] }[] }) => {
    out.push(node.id);
    for (const child of node.children) walk(child as never);
  };
  for (const child of parseDesign(html).root.children) walk(child as never);
  return out;
}

/** The `data-k` this tag already carries, if any. */
function idOf(attrs: string): string | null {
  const match = /\bdata-k\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))/.exec(attrs);
  if (!match) return null;
  return match[1] ?? match[2] ?? match[3] ?? null;
}

/**
 * The file with every node named, so a patch can address it.
 *
 * Idempotent: a document that already has an id on every node comes back byte-identical, which
 * matters because this runs on open and a design that renumbered itself each time you looked at
 * it would produce a diff for doing nothing.
 */
export function materialiseIds(html: string): string {
  const hits = scanTags(html);
  const wanted = mintedOrder(html);
  // Disagreement means the source and the browser read this markup differently. Writing ids
  // under that condition is how a patch lands on a node nobody selected.
  if (hits.length !== wanted.length) return html;

  let out = html;
  // Backwards, so each splice leaves the earlier offsets valid.
  for (let i = hits.length - 1; i >= 0; i--) {
    const hit = hits[i];
    if (idOf(hit.attrs) !== null) continue;
    const at = hit.start + 1 + hit.name.length;
    out = `${out.slice(0, at)} data-k="${wanted[i]}"${out.slice(at)}`;
  }
  return out;
}

/** The declarations of a style attribute, in the order he wrote them. */
function declarationsOf(style: string): [string, string][] {
  const pairs: [string, string][] = [];
  for (const one of splitDeclarations(style)) {
    const colon = one.indexOf(":");
    if (colon < 0) continue;
    const kept = safeDeclaration(one.slice(0, colon), one.slice(colon + 1));
    if (kept) pairs.push(kept);
  }
  return pairs;
}

/**
 * One node's inline style, changed.
 *
 * `null` removes a declaration; anything the parser would refuse is refused here too, because
 * the alternative is writing something to disk that the editor will never show again and a
 * renderer with a laxer whitelist might honour.
 *
 * Only the target tag is rewritten — its style attribute is re-serialised, and every other byte
 * of the file, including every other node's untidy spacing, is left exactly as he left it.
 */
export function applyStyle(html: string, id: string, set: Record<string, string | null>): string {
  const hit = scanTags(html).find((one) => idOf(one.attrs) === id);
  if (!hit) return html;

  const styleMatch = /\s*\bstyle\s*=\s*(?:"([^"]*)"|'([^']*)')/.exec(hit.attrs);
  const existing = declarationsOf(styleMatch?.[1] ?? styleMatch?.[2] ?? "");

  const order: string[] = existing.map(([property]) => property);
  const values = new Map(existing);

  for (const [property, value] of Object.entries(set)) {
    const name = property.trim().toLowerCase();
    if (value === null) {
      values.delete(name);
      continue;
    }
    const kept = safeDeclaration(name, value);
    if (!kept) continue;
    if (!values.has(kept[0])) order.push(kept[0]);
    values.set(kept[0], kept[1]);
  }

  const style = order
    .filter((property) => values.has(property))
    .map((property) => `${property}: ${values.get(property)}`)
    .join("; ");

  const tag = html.slice(hit.start, hit.end);
  let rewritten: string;
  if (styleMatch) {
    // Replace in place, including the whitespace in front of it when the attribute is going.
    const from = tag.indexOf(styleMatch[0]);
    const to = from + styleMatch[0].length;
    rewritten = tag.slice(0, from) + (style ? ` style="${style}"` : "") + tag.slice(to);
  } else if (style) {
    // Last, so it reads as the thing that was added rather than displacing what he wrote.
    const close = tag.endsWith("/>") ? tag.length - 2 : tag.length - 1;
    rewritten = `${tag.slice(0, close)} style="${style}"${tag.slice(close)}`;
  } else {
    return html;
  }

  return html.slice(0, hit.start) + rewritten + html.slice(hit.end);
}

/**
 * Elements with no closing tag, so a scan does not wait for one that never comes.
 *
 * A self-closed tag is handled separately and by its syntax; this is the list of names that are
 * void whether or not he wrote the slash, because `<br>` and `<img src=…>` are how anybody
 * actually writes them.
 */
const VOID = new Set(["br", "hr", "img", "input", "col", "embed", "source", "track", "wbr", "area"]);

/**
 * Where this node begins and ends in the source.
 *
 * Depth-counted over the same tag scan the rest of this file uses, rather than a second parse,
 * so "which node is this" has one answer here. Returns null when the tag never closes — at which
 * point the honest thing is to do nothing, because a move that guessed the end of an element
 * would take an arbitrary amount of the document with it.
 */
function extentOf(html: string, id: string): [number, number] | null {
  const open = scanTags(html).find((one) => idOf(one.attrs) === id);
  if (!open) return null;

  const selfClosed = html.slice(open.start, open.end).endsWith("/>");
  if (selfClosed || VOID.has(open.name)) return [open.start, open.end];

  let depth = 0;
  // A fresh matcher rather than the shared one: `TAG` is global, and advancing its `lastIndex`
  // here would leave it pointing into the middle of a document for whoever scans next.
  const scan = new RegExp(TAG.source, "g");
  scan.lastIndex = open.start;
  for (let match = scan.exec(html); match; match = scan.exec(html)) {
    if (match[0].startsWith("<!--")) continue;
    const name = match[2].toLowerCase();
    if (name !== open.name) continue;
    const closing = match[1] === "/";
    if (!closing) {
      if (!match[0].endsWith("/>") && !VOID.has(name)) depth++;
      continue;
    }
    depth--;
    if (depth === 0) return [open.start, match.index + match[0].length];
  }
  return null;
}

/** The direct children of a node, by id, in document order. */
function childrenOf(html: string, parentId: string): string[] {
  const walk = (node: { id: string; children: { id: string; children: unknown[] }[] }): string[] | null => {
    if (node.id === parentId) return node.children.map((child) => child.id);
    for (const child of node.children) {
      const hit = walk(child as never);
      if (hit) return hit;
    }
    return null;
  };
  return walk(parseDesign(html).root as never) ?? [];
}

/**
 * A node moved to a new place among its siblings, or into a different frame.
 *
 * This is what a drag means in a layout with no coordinates in it: not a position, an order. The
 * node's own text is carried across untouched — its styles, its children, his indentation inside
 * it — so a reorder shows up in the diff as one block that moved rather than two blocks that
 * were rewritten.
 *
 * Cut before measuring where to put it, because the two operations are on the same string and
 * doing it the other way round means every offset past the cut is wrong by the length of what
 * was removed. That bug reads as "dragging works, except downwards".
 */
export function moveNode(html: string, id: string, parentId: string, index: number): string {
  if (id === parentId) return html;
  const extent = extentOf(html, id);
  if (!extent) return html;

  /* The top of the tree is not an element.
   *
   * A design is a list of top-level nodes rather than one root element, so `ROOT_ID` names a
   * holder that exists in the tree and nowhere in the file. Dragging between two top-level
   * sections is an ordinary thing to want, and without this it silently did nothing — there is no
   * `<div data-k="root">` to find the inside of. The document itself is the container. */
  const toRoot = parentId === ROOT_ID;
  const parentExtent = toRoot ? ([0, html.length] as [number, number]) : extentOf(html, parentId);
  if (!parentExtent) return html;
  // Into itself is not a move, it is a document that no longer has a tree.
  if (!toRoot && extent[0] <= parentExtent[0] && parentExtent[1] <= extent[1]) return html;

  const moving = html.slice(extent[0], extent[1]);
  const without = html.slice(0, extent[0]) + html.slice(extent[1]);

  const siblings = childrenOf(without, parentId);
  const before = siblings[Math.max(0, Math.min(index, siblings.length))];

  let at: number;
  if (before) {
    const target = extentOf(without, before);
    if (!target) return html;
    at = target[0];
  } else if (toRoot) {
    at = without.length;
  } else {
    // Last child: just inside the parent's closing tag.
    const holder = extentOf(without, parentId);
    if (!holder) return html;
    const close = without.lastIndexOf("</", holder[1]);
    at = close > holder[0] ? close : holder[1];
  }

  return without.slice(0, at) + moving + without.slice(at);
}

/**
 * One instance turned back into the markup of its part, so this screen can differ from the rest.
 *
 * "This screen only" for the *inside* of an instance. The outside — its padding, its margin —
 * is already per-screen through the `<k-use>`'s own style. A change to a node inside the part
 * cannot be written anywhere but the part's file, which would change every screen; when he
 * means only this one, the honest move is to stop it being an instance here, which is what
 * Figma calls detaching and for the same reason.
 *
 * One level only. A part used *inside* the detached one stays a `<k-use>`, so a detached card
 * still picks up the next change to the button inside it.
 *
 * The part's own `data-k` names are prefixed with the instance's id on the way in, so a part
 * detached twice into one screen does not hand two nodes the same name — two nodes answering to
 * one patch is the failure `materialiseIds` exists to prevent.
 */
export function detachInstance(html: string, id: string, parts: Parts): string {
  const source = materialiseIds(html);
  const extent = extentOf(source, id);
  if (!extent) return html;
  const use = source.slice(extent[0], extent[1]);
  if (!/^<k-use\b/i.test(use)) return html;

  let root = true;
  const flat = expandText(use, parts, {
    deep: false,
    rename: (markup) =>
      markup.replace(/<([a-zA-Z][\w:-]*)((?:"[^"]*"|'[^']*'|[^>"'])*)>/g, (tag, name: string, attrs: string) => {
        if (name.toLowerCase() === "style") return tag;
        const own = /\s*\bdata-k\s*=\s*(?:"([^"]*)"|'([^']*)')/.exec(attrs);
        // The part's first element takes the use's id, so the node he had selected is still
        // the node selected after the screen reloads.
        const next = root ? id : own ? `${id}.${own[1] ?? own[2]}` : null;
        root = false;
        if (next === null) return tag;
        const rest = own ? attrs.replace(own[0], "") : attrs;
        const close = rest.trimEnd().endsWith("/") ? rest.replace(/\s*\/\s*$/, "") : rest;
        return `<${name} data-k="${next}"${close}${close === rest ? "" : " /"}>`;
      }),
  });
  return source.slice(0, extent[0]) + flat + source.slice(extent[1]);
}
