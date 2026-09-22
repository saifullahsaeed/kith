/**
 * A design as a picture — to keep, or to hand him in a message.
 *
 * Two things wanted this and they want the same one thing. A design is the sort of work you
 * send to somebody who does not have the app, and it is the sort of work you point at in the
 * chat when a path alone is not enough: "this bit, here". A path is still the better reference
 * for *him* — he reads the file and sees your edits — but a picture is what a person looks at,
 * and attaching both costs one screenshot.
 *
 * **Rasterised from the DOM, not re-rendered from the source.** `check_design.py` also makes
 * PNGs, and using those would have been less code and quietly wrong: it opens the raw HTML in
 * its own Chromium, which is a different renderer from the one the board draws with, and the
 * gap between the two is where every design-surface bug in this feature has lived. The point
 * of exporting is to hand over *what you are looking at*.
 *
 * The node handed in is the artboard itself, so the picture is the frame and nothing around
 * it: no board dots, no selection outline, no chrome.
 */
import { toBlob } from "html-to-image";

import type { Frame } from "@/lib/design/frame";

/** Twice the frame, so the picture survives being looked at on a good screen. */
const DENSITY = 2;

/**
 * The artboard as a PNG at its declared size.
 *
 * `canvasWidth/Height` are given explicitly because the node on a board is inside a
 * `scale()` — measuring it would capture whatever zoom happened to be set, and a design's
 * picture should be its own size whatever the person was doing with the viewport.
 */
export async function pngOfDesign(node: HTMLElement, frame: Frame): Promise<Blob | null> {
  try {
    return await toBlob(node, {
      width: frame.width,
      height: frame.height,
      // `pixelRatio` already multiplies the canvas. Setting `canvasWidth` as well multiplied it
      // twice: a 390x844 phone came out 1560x3376.
      pixelRatio: DENSITY,
      // Undo any board zoom: the export is of the design, not of the view of it.
      style: { transform: "none", margin: "0" },
      // A design cannot reach the network by construction, so nothing here should either — and
      // a fetch that hangs would hang the export rather than fail it.
      skipFonts: true,
      cacheBust: false,
    });
  } catch {
    return null;
  }
}

/** A filename somebody will still recognise in their downloads folder next week. */
export function pictureName(title: string, frame: Frame): string {
  const stem = (title || "design").replace(/[^\w\- ]+/g, "").trim().replace(/\s+/g, "-").toLowerCase();
  return `${stem || "design"}-${frame.width}x${frame.height}.png`;
}

/** Hand the picture to the browser to save. */
export function savePicture(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  // Revoked on the next tick rather than immediately: Safari has not started reading yet.
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** The blob as something the composer's attachment adapter will take. */
export function asFile(blob: Blob, name: string): File {
  return new File([blob], name, { type: "image/png" });
}
