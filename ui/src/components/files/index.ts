/** Showing a file: the viewer, and the two renderers other surfaces borrow from it.
 *
 * A barrel because this is one feature with one public face — fifteen places open a file and
 * none of them should have to know whether the thing they want is the dialog, the markdown
 * renderer or the kind table. It was one 1,073-line module for exactly that reason; the
 * seams inside it were real, the single entry point was too.
 */
export { FileViewer } from "./viewer";
export { Markdown, MarkdownInline } from "./markdown";
export { CodeBlock } from "./code-block";
export { skipTextRead } from "./kinds";
