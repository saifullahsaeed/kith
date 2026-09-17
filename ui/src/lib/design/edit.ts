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
import { ALLOWED_TAGS, DROP_SUBTREE, parseDesign, safeDeclaration, splitDeclarations } from "@/lib/design/parse";

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
    if (!ALLOWED_TAGS.has(name)) continue;

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
