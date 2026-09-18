import type { ComposerRuntime } from "@assistant-ui/react";

/**
 * A component pointed at in the browser, on its way into the composer.
 *
 * ## Why this is a module and not four lines in the pane
 *
 * The pane's job is measuring a rectangle and drawing chrome; it has no business knowing how an
 * attachment is built. And the interesting part here is not the plumbing — it is what the model
 * is given. A cropped picture on its own is a picture of a card with no idea *which* card, on
 * what page, called what. Every follow-up then costs a round trip to find out, which is exactly
 * the cost the picker exists to remove.
 *
 * So a pick arrives as three things at once: the crop, the selector that will find it again, and
 * the words on it. The selector is the load-bearing one — it is what turns "this component" into
 * something `snap`, `click` and `type` can all address without anybody looking at the page again.
 */
export interface Picked {
  cancelled?: boolean;
  /** Base64 PNG, already cropped and padded by the shell. */
  png?: string;
  width?: number;
  height?: number;
  selector?: string;
  label?: string;
  tag?: string;
  url?: string;
  title?: string;
}

/** A PNG the composer can take. `File` rather than a data URL because that is what
 *  `addAttachment` wants and what a dropped file already is — one path through the adapter,
 *  which is the thing `AnyFileAttachmentAdapter` was rewritten to have. */
export function fileOf(png: string, name: string): File {
  const binary = atob(png);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new File([bytes], name, { type: "image/png" });
}

/** A filename somebody could recognise in a list of attachments. The label first, because "the
 *  Start free trial button" is how a person would refer to it; the tag when there are no words
 *  on it, which is most icons. */
export function nameOf(picked: Picked): string {
  const from = (picked.label || picked.tag || "element")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
  return `${from || "element"}.png`;
}

/**
 * The line that goes in the composer beside the picture.
 *
 * Deliberately not a sentence addressed to him ("here's a component I picked"). It is a caption:
 * the selector, where it is, and what it says. He writes the sentence; this supplies the facts
 * he would otherwise have to spend a call finding out, and the person types their actual
 * question after it.
 */
export function noteFor(picked: Picked): string {
  const parts: string[] = [];
  if (picked.selector) parts.push(`\`${picked.selector}\``);
  else if (picked.tag) parts.push(`\`<${picked.tag}>\``);
  if (picked.width && picked.height) parts.push(`${picked.width}×${picked.height}`);
  const head = parts.join(" · ") || "a component";
  const where = picked.url ? ` on ${picked.url}` : "";
  const says = picked.label ? ` — “${picked.label.slice(0, 80)}”` : "";
  return `[from the browser] ${head}${where}${says}`;
}

/**
 * Put the pick in the focused chat's composer.
 *
 * Appended rather than assigned. Somebody picks a component *while* half-way through typing the
 * question about it — that is the normal order, not the exception — and replacing the box would
 * throw the question away to make room for a caption nobody needed on its own.
 *
 * Returns what the pane should say. `null` for the ordinary success, because a picture that has
 * visibly landed in the composer does not also need to be announced.
 */
export async function sendToChat(picked: Picked, composer: ComposerRuntime | null): Promise<string | null> {
  if (!picked.png) return "Nothing came back from that — try again.";
  if (!composer) {
    return "No chat has focus, so there was nowhere to put it. Open a chat tab and pick again.";
  }
  await composer.addAttachment(fileOf(picked.png, nameOf(picked)));
  const now = composer.getState().text ?? "";
  const note = noteFor(picked);
  composer.setText(now.trim() ? `${now.replace(/\s+$/, "")}\n${note} ` : `${note} `);
  return null;
}
