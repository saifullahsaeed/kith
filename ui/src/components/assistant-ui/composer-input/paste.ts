/**
 * What happens when you paste into the composer.
 *
 * Five rules, in order, and the order is the design. Each one exists because the rule after it
 * would have done the wrong thing with that input.
 *
 * The rule worth explaining is the second. Pasting forty thousand characters of a log into a
 * message box makes the box useless — you cannot see what you are writing, the send button is
 * somewhere below the fold, and the thing you actually wanted to say is buried at the bottom of a
 * wall of someone else's text. So a large paste is lifted out of the message and carried beside
 * it, which is what the PASTED card is. It is not a file you might read later; the server inlines
 * text attachments into what he receives (`_with_attachments`), so pasting a log still hands him
 * the log. The card is about where it sits on screen, not about whether he gets it.
 *
 * Rule four is deliberately narrow. Parsing every paste as markdown would mean prose containing a
 * stray asterisk comes back italicised — silently rewriting what someone pasted is a worse failure
 * than not detecting a list, because the first one is invisible until after it is sent.
 */

import { Slice } from "@tiptap/pm/model";
import type { EditorView } from "@tiptap/pm/view";

import { fromMarkdown } from "./markdown";

/** Past this, a paste is carried beside the message rather than inside it. Two thousand
 *  characters is roughly a screenful of the composer at its tallest — the point where the text you
 *  are writing stops being visible at the same time as the text you pasted. */
export const PASTE_CHAR_LIMIT = 2_000;

/** And a line count, because a hundred short lines fills the box just as completely as one long
 *  paragraph while being nowhere near the character limit. */
export const PASTE_LINE_LIMIT = 30;

export function isOversized(text: string): boolean {
  return text.length > PASTE_CHAR_LIMIT || text.split("\n").length > PASTE_LINE_LIMIT;
}

/**
 * Does this text mean its markdown, or merely contain some?
 *
 * Structural marks only, anchored to the start of a line: a heading, a list bullet, a quote, a
 * fence, or a table row. Inline syntax is deliberately not enough — `2 * 3` and `snake_case` are
 * far more common in a message to him than emphasis is, and getting this wrong rewrites what
 * someone pasted without telling them.
 */
export function looksLikeMarkdown(text: string): boolean {
  return /^\s{0,3}(#{1,6} |[-*+] |\d+\. |> |```|\|.*\|)/m.test(text);
}

/** A pasted block, as a file the composer can carry. Named for what it is — the name is what he
 *  is told the text came from, and what the card shows. */
export function pastedFile(text: string, ordinal: number): File {
  return new File([text], `pasted-${ordinal}.txt`, { type: "text/plain" });
}

/** A file that came from a paste rather than from the disk. The strip shows these differently —
 *  a preview of the text with a PASTED badge, rather than a filename nobody chose. */
export function isPastedFile(name: string): boolean {
  return /^pasted-\d+\.txt$/.test(name);
}

type PasteContext = {
  /** Hands a file to the composer's attachment pipeline. */
  attach(file: File): void;
  /** How many pastes this composer has carried, for naming. */
  ordinal(): number;
};

/**
 * The handler ProseMirror calls. `true` means handled — ProseMirror does nothing further.
 *
 * Returning `false` is a real answer and not a fallthrough failure: it hands the paste back to
 * ProseMirror, which knows how to insert plain text and how to read the clipboard's own HTML.
 * That is the right outcome for most pastes and this should not try to beat it.
 */
export function handlePaste(view: EditorView, event: ClipboardEvent, context: PasteContext): boolean {
  const clipboard = event.clipboardData;
  if (!clipboard) return false;

  // 1. Files. Unchanged from what the textarea did — a screenshot on the clipboard is the other
  //    thing people try after dragging, and Cmd-V otherwise does nothing at all for an image.
  const files = Array.from(clipboard.files);
  if (files.length) {
    event.preventDefault();
    for (const file of files) context.attach(file);
    return true;
  }

  const text = clipboard.getData("text/plain");
  if (!text) return false;

  // 2. Too large to type beside. Out of the message, into a card.
  if (isOversized(text)) {
    event.preventDefault();
    context.attach(pastedFile(text, context.ordinal()));
    return true;
  }

  // 3. Something that was code where it came from. Kept verbatim in a block rather than run
  //    through a markdown parser that would read its underscores as emphasis.
  const html = clipboard.getData("text/html");
  if (html && /<pre[\s>]/i.test(html)) {
    event.preventDefault();
    const { schema } = view.state;
    view.dispatch(
      view.state.tr.replaceSelectionWith(
        schema.nodes.codeBlock.create(null, text ? schema.text(text) : null),
      ),
    );
    return true;
  }

  // 4. Markdown, and only when it means it.
  if (looksLikeMarkdown(text)) {
    event.preventDefault();
    // `maxOpen` rather than a closed slice: it lets a single pasted paragraph flow into the
    // sentence the caret is already in, while a list or a table still arrives as its own blocks.
    // A closed slice would break the line in half around anything you pasted mid-sentence.
    view.dispatch(view.state.tr.replaceSelection(Slice.maxOpen(fromMarkdown(text).content)));
    return true;
  }

  // 5. Everything else is ProseMirror's, which handles it better than this would.
  return false;
}
