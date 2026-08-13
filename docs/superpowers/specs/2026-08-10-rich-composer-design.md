# A composer you can give structure to

**Date:** 2026-08-10
**Status:** approved, in build

## The problem

The composer is a `<textarea>` holding one plain string, and user messages render that string
verbatim. So a numbered spec, a table, or a pasted file all arrive as one undifferentiated blob —
in the box while writing it, in the bubble afterwards, and in the prompt he reads. Structure a
person can see is structure he can use, and there was nowhere to put any.

## What it becomes

A Tiptap (ProseMirror) editor in the main composer. Headings, lists, tables, blockquotes,
emphasis, links, code blocks. The bubble renders what you wrote. He receives raw markdown.

**Scope: the main composer only.** Message editing keeps its textarea — it is separately broken
(the edit composer opens and closes; see "Known, out of scope") and fixing it is its own job. The
question-answer box stays plain: it is a one-line reply field.

## The seam

One canonical value survives all of this: **the composer store still holds a markdown string.**
Nothing outside the editor learns that the input got richer — not the wire format, not the slash
menu, not the quote strip, not the context ledger.

* **Editor → store.** Serialise the doc, `setText(markdown)` through
  `unstable_useComposerInput()`.
* **Store → editor.** `/skill` writes text, quotes insert text, history restores it. Re-parse into
  the doc, but *only* when the incoming value is not the one we just wrote — otherwise the editor
  rebuilds itself on every keystroke and eats the caret.

### Debounce, and the two things it must not break

Serialising on every keystroke is wasted work on a long message, so writes above ~4k characters
are trailing-debounced. Below that they are immediate, because two things read the store text
*synchronously* and a lagging value would break both:

* the slash menu filters on `s.composer.text`, and `/` is typed into a small document;
* the send button reads the store, not the editor.

So: immediate under the threshold, debounced above it, and an unconditional flush before send, on
blur, and on any pointer-down (which is how the send button gets a fresh value without the editor
knowing the button exists).

### Keeping the slash menu

`ComposerPrimitive.Input` feeds the trigger popover internally; a custom input has to do it by
hand, through `unstable_useTriggerPopoverTriggers()`:

* every keydown goes to each trigger's `resource.handleKeyDown` **first**, so Enter runs a
  highlighted command instead of sending;
* every selection change reports `resource.setCursorPosition(offset)`, where offset is
  `serialise(doc.cut(0, caret)).length` — how many characters of markdown precede the caret;
* `unstable_useTriggerPopoverAriaProps()` is spread onto the editor element.

## Paste, in order

1. **Files on the clipboard** — the existing attachment path, unchanged.
2. **Text over the limit** (2,000 characters or 30 lines, whichever first) — becomes a
   `pasted-N.txt` attachment and shows as a PASTED card in the strip, previewing its first lines.
3. **Clipboard HTML containing `<pre>`** — a code block, verbatim.
4. **Text that looks like markdown** — parsed into real nodes, so a pasted table lands as a table.
   Deliberately not "parse everything": prose containing a stray `*` is prose.
5. **Anything else** — ProseMirror's own handling.

## The pasted text has to reach him

`_with_attachments` writes every non-image attachment into `inbox/` and tells him the *path*. For
a pasted block that is the wrong answer — he would spend a tool call opening what you just handed
him, and the point of pasting is to hand it over.

So **`text/*` attachments are inlined** into the message as a named fenced block, and still
written to `inbox/` so they stay on disk and clickable. Inlined up to a ceiling; past it he gets
the head, the path for the rest, and a sentence saying that is what happened. A paste too big to
carry must say so — never silently truncate.

## The bubble

User messages render through markdown (`react-markdown` + `remark-gfm`, both already in the tree),
styled for the bubble rather than the reply column. He receives the raw markdown unchanged: that
falls out for free once the canonical value is markdown.

## Must not break

Slash menu · quote strip · image paste · drag-and-drop · Enter and Shift-Enter · autofocus · the
box's growth cap · the context meter · `/skill` writing into the composer.

## Testing

Vitest, already set up. Unit tests for the markdown round-trip (including tables), the caret
offset, and each paste rule against a fake clipboard. Server tests for text inlining and the
ceiling. The feel of it is manual.

## Known, out of scope

* **Message editing opens and closes.** A real bug, unrelated to this, in the edit composer.
* **No toolbar.** Input rules and Cmd-B/I cover everything except table row and column
  management, which stays keyboard-only.
