import type { Edge } from "./tree";

/**
 * Where a tab would land if you let go here.
 *
 * Native HTML5 drag-and-drop rather than pointer events, and that is a decision worth its
 * paragraph. Pointer events give total control and cost a drag preview, a pointer-capture
 * lifecycle, an auto-scroll, and a set of edge cases around losing the pointer to another
 * window — several hundred lines whose failure mode is exactly the cheapness this feature was
 * asked not to have. HTML5 DnD gives the drag image, the cursor and the cancel-on-escape for
 * free, and its real weakness is touch, on a macOS desktop app. What it does not give is the
 * drop *target*, and that is the twenty lines below.
 */

/** How much of a pane's width or height counts as its edge rather than its middle.
 *
 * A quarter. Smaller and a deliberate split needs precision you do not have mid-drag; larger
 * and a pane narrower than about 400px has no middle left, so dropping a tab into an existing
 * strip becomes the hard target instead of the easy one. */
const EDGE = 0.25;

/** The pointer, a pane's rectangle, and which of the five drops that is.
 *
 * Whichever edge the pointer is nearest *and* inside the band of, so a corner resolves to the
 * one it is deepest into rather than to whichever happens to be tested first — a corner that
 * silently favours "left" every time is a layout you cannot build a bottom split from without
 * aiming away from the corner you are looking at.
 */
export function edgeAt(rect: DOMRect, x: number, y: number): Edge {
  if (rect.width <= 0 || rect.height <= 0) return "center";
  const left = (x - rect.left) / rect.width;
  const top = (y - rect.top) / rect.height;
  const distances: [Edge, number][] = [
    ["left", left],
    ["right", 1 - left],
    ["top", top],
    ["bottom", 1 - top],
  ];
  const nearest = distances.reduce((best, one) => (one[1] < best[1] ? one : best));
  return nearest[1] < EDGE ? nearest[0] : "center";
}

/** Which gap in a strip of tabs the pointer is over, as an "insert before" index.
 *
 * The pane body answers "which edge"; this answers the question the *strip* is asked, which is
 * not an edge at all but a position in an order — the drop that sorts tabs instead of the drop
 * that only ever appended. The rule is the one every editor tab strip uses: the left half of a
 * tab means "before it", the right half "after it", and past the last tab means the end, so the
 * pointer rests between two tabs rather than snapping to whichever tab it happens to cover.
 *
 * Takes the tabs' rectangles in order rather than an element, so it is arithmetic and testable
 * without a live strip; the caller reads `getBoundingClientRect` off each tab button. */
export function insertIndexAt(tabs: { left: number; right: number }[], x: number): number {
  for (let index = 0; index < tabs.length; index++) {
    if (x < (tabs[index].left + tabs[index].right) / 2) return index;
  }
  return tabs.length;
}

/** Where a tab dragged from `from` lands when dropped before `before`.
 *
 * The caret is drawn between the tabs *as they are on screen*, before the move; removing the
 * dragged tab shifts every tab after it left by one, so a caret on the dragged tab's right half
 * — or anywhere past it — lands one earlier than the caret says. A caret at the tab's own left
 * edge, which is where "before itself" points, lands where it started: a no-op drop, which is
 * the honest answer for a drag that did not go anywhere. */
export function landingIndex(from: number, before: number): number {
  return from < before ? before - 1 : before;
}

/** The MIME-ish key the drag carries.
 *
 * A custom type rather than `text/plain`: a tab dragged out of the window and into a text
 * editor should not paste "chat:20260803-110800342-734b7a", and a file dragged *in* from Finder
 * must not look like a tab. `dataTransfer.types` is readable during `dragover`, which is what
 * lets a pane light up only for a drag it can actually accept. */
export const TAB_MIME = "application/x-kith-tab";

/** Is this drag one of ours? Asked on every `dragover`, where the payload cannot be read. */
export function carriesTab(transfer: DataTransfer | null): boolean {
  return !!transfer && Array.from(transfer.types).includes(TAB_MIME);
}

/** Where the highlight goes for a pending drop. Percentages, so the caller can put it straight
 *  into a style without knowing the pane's size. */
export function highlightFor(edge: Edge): {
  left: string;
  top: string;
  width: string;
  height: string;
} {
  switch (edge) {
    case "left":
      return { left: "0%", top: "0%", width: "50%", height: "100%" };
    case "right":
      return { left: "50%", top: "0%", width: "50%", height: "100%" };
    case "top":
      return { left: "0%", top: "0%", width: "100%", height: "50%" };
    case "bottom":
      return { left: "0%", top: "50%", width: "100%", height: "50%" };
    default:
      return { left: "0%", top: "0%", width: "100%", height: "100%" };
  }
}
