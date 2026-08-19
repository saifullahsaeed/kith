import remarkGfm from "remark-gfm";
import rehypeKatex from "rehype-katex";
import { remarkBr } from "@/lib/remark-br";
import { remarkTexSymbols } from "@/lib/remark-tex-symbols";

/**
 * The remark plugins, in one place, because there are four renderers and they had drifted.
 *
 * His replies, a file in the viewer (block and inline), and your own message bubble all render
 * the same markdown, and the list was written out separately at each call site. The bubble had
 * fallen a plugin behind — `<br>` inside a table you composed arrived as the literal text
 * `<br>`, in the one renderer whose input is a rich editor that can make tables.
 *
 * A shared const rather than four arrays that have to be remembered together.
 */
export const MARKDOWN_PLUGINS = [remarkGfm, remarkBr, remarkTexSymbols];

/**
 * Typesetting for the maths `remarkTexSymbols` lifted out.
 *
 * Split from the remark list because these run on the other side of the pipeline: remark shapes
 * the markdown, rehype shapes the HTML it became. `rehype-katex` looks for the `math-inline`
 * spans the remark plugin emits and replaces them with typeset output.
 */
export const MARKDOWN_REHYPE = [rehypeKatex];
