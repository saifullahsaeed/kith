/**
 * Turning a rendered diagram into something other than a picture on this page.
 *
 * Separated from the component because neither of these is about React, and because a function
 * that only exists inside a `.tsx` can only be checked by looking at it. `naturalSize` in
 * particular was the direct cause of a bug that was reported as "expand makes it smaller", and
 * a thing that has been wrong once is a thing worth being able to run.
 */

/**
 * Mermaid's SVG at its own size, and what that size is.
 *
 * `render` returns an SVG carrying `style="max-width: …px"` and `width="100%"`. That is right
 * inline — it is how a diagram fits a column — and wrong everywhere else. Expanding one made it
 * *smaller*, and the reason is worth writing down: the lightbox fought that with `max-w-none`,
 * which is a class, and an inline style beats a class. So the constrained SVG landed inside a
 * shrink-to-fit box with nothing to stretch against, and collapsed.
 *
 * Parsed rather than pattern-matched. The attribute is generated and predictable; the document
 * around it is a few thousand paths and labels, and regexing through XML is how you eventually
 * eat something you did not mean to.
 *
 * Returns the markup unchanged with a zero size when there is nothing sensible to do — no
 * viewBox, or not an SVG at all — so callers have one thing to test rather than an exception
 * to catch.
 */
export function naturalSize(markup: string): { html: string; width: number; height: number } {
  const unchanged = { html: markup, width: 0, height: 0 };
  try {
    const doc = new DOMParser().parseFromString(markup, "image/svg+xml");
    // A parse failure is a document *containing* `<parsererror>` rather than an exception.
    if (doc.getElementsByTagName("parsererror").length) return unchanged;
    const svg = doc.documentElement;
    if (svg.nodeName.toLowerCase() !== "svg") return unchanged;
    const box = (svg.getAttribute("viewBox") ?? "").split(/[\s,]+/).map(Number);
    const width = Number.isFinite(box[2]) && box[2] > 0 ? box[2] : 0;
    const height = Number.isFinite(box[3]) && box[3] > 0 ? box[3] : 0;
    if (!width || !height) return unchanged;
    svg.removeAttribute("style");
    svg.setAttribute("width", String(width));
    svg.setAttribute("height", String(height));
    return { html: new XMLSerializer().serializeToString(svg), width, height };
  } catch {
    return unchanged;
  }
}

/**
 * The drawing as a PNG, for the clipboard.
 *
 * A picture, because that is what "copy the diagram" means everywhere a diagram exists — you
 * paste it into a document and see the diagram. The source is what the fenced block already
 * gives anyone who wants it.
 *
 * Painted onto the page background rather than left transparent: a transparent PNG of pale text
 * pasted into a white document is a blank rectangle, and the person who copied it saw something
 * legible. What you paste should be what you were looking at.
 *
 * Null rather than throwing, on every path that can fail — no viewBox, an SVG the image decoder
 * refuses, no 2D context. The caller has to tell the person it did not work either way, and one
 * return value is easier to be honest with than a mix of null and exceptions.
 */
export async function toPng(
  markup: string,
  background: string,
  scale = 2,
): Promise<Blob | null> {
  const { html, width, height } = naturalSize(markup);
  if (!width || !height) return null;

  const image = new Image();
  const loaded = new Promise<boolean>((resolve) => {
    image.onload = () => resolve(true);
    image.onerror = () => resolve(false);
  });
  // A data URI rather than a blob URL: an SVG loaded as a blob counts as cross-origin for this
  // purpose, which taints the canvas and makes `toBlob` throw when it is read back.
  image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(html)}`;
  if (!(await loaded)) return null;

  const canvas = document.createElement("canvas");
  canvas.width = Math.round(width * scale);
  canvas.height = Math.round(height * scale);
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.scale(scale, scale);
  ctx.fillStyle = background;
  ctx.fillRect(0, 0, width, height);
  ctx.drawImage(image, 0, 0, width, height);
  return await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
}
