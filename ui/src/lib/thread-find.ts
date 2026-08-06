/**
 * Finding a word in a conversation.
 *
 * Reads the rendered DOM rather than the message data, which is the whole reason the count
 * and the highlight can never disagree. The data is markdown: `**check**point` is one word on
 * screen and two runs of characters in the source, and the source carries `**` the screen does
 * not — so a count taken from the data would report matches that cannot be highlighted and
 * miss ones that can. The screen is what a person is searching.
 *
 * Off-screen messages are covered. Both message components carry `[content-visibility:auto]`,
 * which skips *rendering* — the text nodes exist either way, so a `TreeWalker` sees the whole
 * conversation whether or not it has ever been scrolled into view.
 *
 * Ranges rather than injected `<mark>` elements: the caller hands these to the CSS Custom
 * Highlight API, so nothing is inserted into a tree React owns and rendered markdown cannot be
 * mangled by a highlight landing mid-element.
 */

/** The containers that hold prose. An allowlist, not a denylist, and that direction is the
 *  point: if a new kind of part appears in a message, the failure here is "find stops seeing
 *  some writing" — visible, annoying, harmless. A denylist would fail the other way, silently
 *  matching tool output, which is the rule `_spoken` sets on the server ("matching them would
 *  answer 'where did we talk about X' with a stack trace") breaking without a sound. */
const PROSE = ['[data-slot="aui_assistant-message-content"]', ".aui-user-message-content"];

/** Pruned from inside those containers. `aui_chain-of-thought` is the rail holding reasoning
 *  and tool groups, which sits *inside* the assistant's content; the other two catch a
 *  reasoning block or tool card rendered standalone, outside a rail. */
const NOT_PROSE = [
  '[data-slot="aui_chain-of-thought"]',
  '[data-slot="reasoning-root"]',
  '[data-slot="tool-group-root"]',
];

/** Shortest query worth running, matching the history panel: one letter matches everything and
 *  tells you nothing. */
export const MIN_QUERY = 2;

/**
 * Every occurrence of `query` in the conversation's prose, in reading order.
 *
 * Document order is thread order, so walking the DOM gives ordered matches for free — no
 * sorting, and no need to know a message's index.
 *
 * Case-insensitive and literal: no regex, no word boundaries. A person typing into a find bar
 * means "these characters", and a query containing `(` should find `(` rather than throw.
 */
export function findRanges(root: HTMLElement | null, query: string): Range[] {
  const needle = query.trim().toLowerCase();
  if (!root || needle.length < MIN_QUERY) return [];

  const ranges: Range[] = [];
  for (const container of root.querySelectorAll<HTMLElement>(PROSE.join(","))) {
    // A nested prose container would be walked twice, once by each ancestor. Not possible
    // today — a user message never contains an assistant one — but cheap to be sure of.
    if (container.parentElement?.closest(PROSE.join(","))) continue;
    collect(container, needle, ranges);
  }
  return ranges;
}

/** What counts as one run of continuous reading. Markdown emits these; a match may not span
 *  two of them. Anything else — `<strong>`, `<em>`, `<code>`, `<a>` — is inline, and text is
 *  joined straight across it. */
const BLOCK = "p,li,h1,h2,h3,h4,h5,h6,blockquote,pre,td,th,dd,dt,figcaption";

/** One text node's place in the block it belongs to. */
type Piece = { node: Text; from: number };

/**
 * Every occurrence inside one prose container.
 *
 * Text is joined per block before it is searched, rather than each text node being searched on
 * its own. `**check**point` is one word on screen and two text nodes underneath — "check" inside
 * a `<strong>`, "point" after it — so a per-node scan cannot find "checkpoint" in it at all.
 * That was the case used to argue for reading the DOM instead of the markdown source in the
 * first place, so it had better work.
 *
 * Joined per *block* and not per container, which is what stops the fix creating a new problem:
 * concatenating a whole reply into one string would let a query match across the gap between two
 * paragraphs, finding a "word" that exists nowhere on screen.
 */
function collect(container: HTMLElement, needle: string, into: Range[]): void {
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = node.parentElement;
      if (!parent) return NodeFilter.FILTER_REJECT;
      // Tested per text node, walking up from each one, rather than pruning a subtree on the way
      // in. `SHOW_TEXT` means this filter only ever sees text nodes, and a text node has no
      // children — so REJECT and SKIP mean the same thing here and there is no subtree to prune.
      // Every text node inside a rail has to disqualify itself, which `closest` fits exactly.
      if (parent.closest(NOT_PROSE.join(","))) return NodeFilter.FILTER_REJECT;
      return node.nodeValue ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
    },
  });

  // Grouped by block, keyed on the element itself — insertion-ordered, so blocks come out in
  // document order and so do the matches within them.
  const blocks = new Map<Element, Piece[]>();
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node as Text;
    const block = text.parentElement?.closest(BLOCK) ?? container;
    const pieces = blocks.get(block);
    if (pieces) pieces.push({ node: text, from: 0 });
    else blocks.set(block, [{ node: text, from: 0 }]);
  }

  for (const pieces of blocks.values()) {
    let joined = "";
    for (const piece of pieces) {
      piece.from = joined.length;
      joined += piece.node.nodeValue ?? "";
    }
    const haystack = joined.toLowerCase();

    // `from = at + needle.length`, so "aa" in "aaaa" is two matches rather than three. Someone
    // walking matches expects them not to overlap.
    for (let from = 0; from <= haystack.length - needle.length; ) {
      const at = haystack.indexOf(needle, from);
      if (at < 0) break;
      const range = document.createRange();
      const start = locate(pieces, at);
      const end = locate(pieces, at + needle.length);
      if (start && end) {
        range.setStart(start.node, start.offset);
        range.setEnd(end.node, end.offset);
        into.push(range);
      }
      from = at + needle.length;
    }
  }
}

/** Which text node a position in the joined string falls in, and where in that node.
 *
 * Walks backwards so the *last* piece starting at or before `at` wins. Forwards would stop on an
 * empty piece — one contributing no characters, so starting exactly at `at` — and hand back a
 * node the range cannot be anchored in. */
function locate(pieces: Piece[], at: number): { node: Text; offset: number } | null {
  for (let i = pieces.length - 1; i >= 0; i--) {
    const piece = pieces[i];
    const length = piece.node.nodeValue?.length ?? 0;
    if (at >= piece.from && at <= piece.from + length) {
      return { node: piece.node, offset: at - piece.from };
    }
  }
  return null;
}

/** Whether this browser can paint highlights. Chromium always can, which is what ships; an
 *  older Safari cannot, and there the bar still counts and still scrolls — only the tint is
 *  missing, which is a degraded find rather than a broken one. */
export const canHighlight = (): boolean => typeof CSS !== "undefined" && "highlights" in CSS;

const ALL = "kith-find";
const CURRENT = "kith-find-current";

/**
 * Paint `ranges`, with `current` picked out from the rest.
 *
 * Two registrations rather than one, because the current match has to look different from its
 * neighbours — that is the only thing telling you which of seventeen you are standing on. The
 * current range is deliberately left in both: `::highlight()` paints in registration order, so
 * the specific one drawn second simply covers the general one.
 */
export function paintRanges(ranges: Range[], current: number): void {
  if (!canHighlight()) return;
  const highlights = CSS.highlights;
  if (ranges.length === 0) {
    highlights.delete(ALL);
    highlights.delete(CURRENT);
    return;
  }
  highlights.set(ALL, new Highlight(...ranges));
  const one = ranges[current];
  if (one) highlights.set(CURRENT, new Highlight(one));
  else highlights.delete(CURRENT);
}

/** Take the paint off. Called on close and on unmount — `CSS.highlights` is global to the
 *  document, so a registration left behind outlives the component that made it. */
export function clearHighlights(): void {
  if (!canHighlight()) return;
  CSS.highlights.delete(ALL);
  CSS.highlights.delete(CURRENT);
}

/**
 * Bring a match into view, centred.
 *
 * Twice, a frame apart, and that is not belt-and-braces. A message that has never been on
 * screen is `content-visibility: auto` with `contain-intrinsic-size: auto 200px` — the browser
 * is guessing its height at 200px until it actually lays it out. The first scroll is computed
 * against those guesses and lands somewhere near; laying the message out then changes every
 * offset below it, so the second scroll is the one that lands. It is the same correction the
 * viewport's own `autoScroll` note describes for scroll-to-bottom.
 *
 * Centred rather than top-aligned: a match with no visible text above it reads as though the
 * conversation starts there, and the sentence around a word is usually why you were looking.
 */
export function scrollToRange(range: Range): void {
  const target =
    range.startContainer.nodeType === Node.ELEMENT_NODE
      ? (range.startContainer as HTMLElement)
      : range.startContainer.parentElement;
  if (!target) return;
  // `behavior: "instant"` is load-bearing, not a preference. The viewport sets
  // `scroll-smooth`, and CSS `scroll-behavior` applies to `scrollIntoView` unless it is
  // overridden here — so stepping to a match a few hundred messages away *animated* the whole
  // way there, laying out every `content-visibility: auto` message it passed through, each one
  // holding a dozen-odd tool cards. On a 473-message conversation that is a second or more of
  // solid layout work per keypress, and it reads as the entire app seizing up. A find bar wants
  // to arrive, not to travel.
  const go = () => target.scrollIntoView({ block: "center", inline: "nearest", behavior: "instant" });
  go();
  // Once more next frame, for the estimated-height problem: a message that has never been on
  // screen is 200px of `contain-intrinsic-size` guess until it lays out, so the first jump is
  // computed against fiction and lands near. Cheap now that neither call animates.
  requestAnimationFrame(() => {
    if (target.isConnected) go();
  });
}
