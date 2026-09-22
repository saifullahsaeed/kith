/**
 * Pointing at a design from the chat, so he can read the one you are looking at.
 *
 * The gap this closes is narrow and was constant. You open a screen on the board, you change
 * the row height by hand, you want to say "now build this" — and the only way to tell him
 * *which* was to remember the path and type it. Designs live several folders down under
 * `.kith/screens/`, the board shows a title and never the path, and a path typed from memory is
 * a path he cannot open. So the thing you were looking at was the one thing you could not
 * mention.
 *
 * What goes into the composer is the path, not the markup. He has `read_file` and the file is
 * on disk — pasting the HTML would put a stale copy in the transcript, cost thousands of tokens
 * and, worst of the three, be wrong the moment you touch the design again. A path is read when
 * he gets to it, which is the only way the answer reflects your edits.
 *
 * `activeComposer()` is the same slot a dropped file uses, for the same reason: with a composer
 * per chat pane there is no single `useComposerRuntime()` to reach, and the board is an overlay
 * sitting above all of them.
 */
import { activeComposer } from "@/lib/active-composer";

/** How a design reads when it arrives in a message. */
export function referenceFor(path: string, title?: string): string {
  const named = (title || "").trim();
  return named ? `${named} — \`${path}\`` : `\`${path}\``;
}

/**
 * Attach a picture of the design to the focused chat, beside the reference.
 *
 * Both, not either. The path is what *he* reads — it is the file, so it reflects the edits you
 * just made by hand — and the picture is what a person scrolling the thread actually sees. One
 * screenshot buys both readers.
 */
export async function attachPicture(file: File): Promise<boolean> {
  const composer = activeComposer();
  if (!composer) return false;
  try {
    await composer.addAttachment(file);
    return true;
  } catch {
    return false;
  }
}

/**
 * Put a reference to this design in the focused chat's composer, and say whether it landed.
 *
 * Appended rather than assigned: the usual move is to type the question first and point at the
 * screen second, and replacing what you had written would be the worse failure of the two.
 */
export function referenceInChat(path: string, title?: string): boolean {
  const composer = activeComposer();
  if (!composer) return false;

  const reference = referenceFor(path, title);
  const held = composer.getState().text;
  // One blank line between what you wrote and what you pointed at, and never a second copy of
  // the same design — clicking twice is a slip, not a request for two.
  if (held.includes(`\`${path}\``)) return true;
  composer.setText(held.trim() ? `${held.trimEnd()}\n\n${reference}` : reference);
  return true;
}
