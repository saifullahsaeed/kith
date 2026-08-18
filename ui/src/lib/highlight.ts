import hljs from "highlight.js/lib/common";

/**
 * Code, coloured — one implementation for everywhere code is shown.
 *
 * This lived inside `components/files/code-block.tsx` and was reachable only from the file
 * viewer, so the app coloured a file you opened and rendered the same language as flat grey when
 * he wrote it in a reply. Two code renderers, one of them lit. The theme in `index.css` was
 * already written against the app's own palette and only half the app was using it.
 *
 * `highlight.js` was already a dependency; this adds no package, it reaches the renderer that
 * was missing.
 */

/** Past this, colouring costs more than it is worth and risks a visible stall. */
const TOO_BIG = 300_000;

/**
 * The grammar for a fence's info string, or null if there is no such grammar.
 *
 * Callers rendering *streaming* code must check this and fall back to plain text, because the
 * alternative — `highlightAuto` — is the wrong tool twice over on a half-written block. Measured
 * against a ~9.6KB block: a named grammar is 1.3ms, auto-detection is 31.6ms, and a chat block
 * re-renders on every token. Worse than the cost, the guess *changes* as the block grows, so the
 * colours flicker between languages while you watch it type.
 *
 * Nearly free to obey: across his 224 transcripts, 694 of 698 fenced blocks name a language, and
 * the commonest label is `text`, which resolves here to Plain text and escapes without colouring.
 */
export function grammarFor(language: string | undefined): string | null {
  if (!language) return null;
  return hljs.getLanguage(language) ? language : null;
}

/**
 * Colour `code`. With no `language` this detects one, which is right for a whole file sitting
 * still and wrong for anything mid-stream — see `grammarFor`.
 */
export function highlight(code: string, language?: string): string {
  try {
    if (code.length > TOO_BIG) return escapeHtml(code);
    if (language && hljs.getLanguage(language)) {
      return hljs.highlight(code, { language, ignoreIllegals: true }).value;
    }
    return hljs.highlightAuto(code).value;
  } catch {
    // Never let a grammar bug take the message with it: unlit code still reads.
    return escapeHtml(code);
  }
}

export function escapeHtml(s: string): string {
  return s.replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string,
  );
}
