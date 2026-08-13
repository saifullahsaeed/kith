/**
 * The one place the editor's document and markdown meet.
 *
 * The composer is a rich editor now, but the value everything else reads is still a markdown
 * string: the wire format sends it, the slash menu filters on it, the context ledger measures it,
 * and he reads it. That is deliberate — a tree in the store would mean every one of those learning
 * about ProseMirror. So the editor owns a document, this module owns the translation, and the
 * store owns a string.
 *
 * Both directions live here together on purpose. A parser and a serialiser that drift apart are
 * worse than either being wrong on its own: text goes in, comes out subtly different, and the
 * difference lands in a message you already sent. Round-tripping is what the tests check.
 *
 * `prosemirror-markdown` supplies the serialiser, whose node map is keyed by *its* node names
 * (`bullet_list`, `code_block`) rather than Tiptap's (`bulletList`, `codeBlock`) — hence the
 * explicit map below rather than spreading its defaults. The parser is written out by hand
 * instead of using `MarkdownParser`, because tables are exactly where the generic path gives up:
 * a cell's content arrives as inline tokens, and a Tiptap cell holds blocks, so the inline run
 * has to be wrapped in a paragraph on the way in. That is one special case in a walker I control
 * and an unfixable mismatch in one I do not.
 */

import { getSchema } from "@tiptap/core";
import Link from "@tiptap/extension-link";
import { TableKit } from "@tiptap/extension-table";
import StarterKit from "@tiptap/starter-kit";
import type { Node as ProseNode, Schema } from "@tiptap/pm/model";
import { MarkdownSerializer, defaultMarkdownSerializer } from "prosemirror-markdown";
import MarkdownIt, { type Token } from "markdown-it";

/**
 * What the composer can hold — and therefore what it can be asked to write back out.
 *
 * Nothing here that markdown cannot express. Underline is off for that reason: it renders, it
 * feels like it works, and then it serialises to nothing and the emphasis you applied is gone
 * from the message you sent. A formatting control that silently discards its own effect is worse
 * than not having it.
 *
 * Headings stop at three. This is a message to someone, not a document.
 */
export const COMPOSER_EXTENSIONS = [
  StarterKit.configure({
    heading: { levels: [1, 2, 3] },
    underline: false,
    // StarterKit's own link is off so it can be configured here instead — one link extension, not
    // two, which the schema would reject outright.
    link: false,
  }),
  // Not `openOnClick`: this is an input. Clicking a link you are still writing should put the
  // caret in it, not navigate away and lose the message.
  Link.configure({ openOnClick: false, autolink: true }),
  TableKit.configure({ table: { resizable: false } }),
];

/** The schema those extensions describe, built once and without an editor — the tests and the
 *  caret arithmetic both need it outside React. */
export const composerSchema: Schema = getSchema(COMPOSER_EXTENSIONS);

// --------------------------------------------------------------------------- //
// Document → markdown
// --------------------------------------------------------------------------- //

/** A table, written out as GFM pipes.
 *
 *  `prosemirror-markdown` has no table support at all, so the whole node is rendered here rather
 *  than by the walker. Cells are serialised through the same serialiser — a cell holds a
 *  paragraph, and a paragraph is something this file already knows how to write — then flattened
 *  to one line, because a pipe table has no way to express a line break inside a cell.
 */
function tableToMarkdown(state: { write(s: string): void; closeBlock(n: ProseNode): void }, node: ProseNode) {
  const rows: string[][] = [];
  node.forEach((row) => {
    const cells: string[] = [];
    row.forEach((cell) => cells.push(cellToMarkdown(cell)));
    rows.push(cells);
  });
  if (!rows.length) return;

  // GFM requires a header row. A table pasted without one still has to come back out as a valid
  // table, so the first row becomes the header — losing the distinction is better than emitting
  // pipes that no renderer will read as a table.
  const width = Math.max(...rows.map((row) => row.length));
  const pad = (cells: string[]) => [...cells, ...Array(width - cells.length).fill("")];
  const line = (cells: string[]) => `| ${pad(cells).join(" | ")} |`;

  state.write(line(rows[0]) + "\n");
  state.write(`| ${Array(width).fill("---").join(" | ")} |\n`);
  for (const row of rows.slice(1)) state.write(line(row) + "\n");
  state.closeBlock(node);
}

function cellToMarkdown(cell: ProseNode): string {
  const inner = composerSchema.topNodeType.create(null, cell.content);
  return serializer
    .serialize(inner, { tightLists: true })
    .trim()
    .replace(/\s*\n+\s*/g, " ")
    // A literal pipe would end the cell early and shift every column after it.
    .replace(/\|/g, "\\|");
}

const serializer = new MarkdownSerializer(
  {
    doc: defaultMarkdownSerializer.nodes.doc,
    text: defaultMarkdownSerializer.nodes.text,
    paragraph: defaultMarkdownSerializer.nodes.paragraph,
    heading: defaultMarkdownSerializer.nodes.heading,
    blockquote: defaultMarkdownSerializer.nodes.blockquote,
    horizontalRule: defaultMarkdownSerializer.nodes.horizontal_rule,
    hardBreak: defaultMarkdownSerializer.nodes.hard_break,
    codeBlock(state, node) {
      state.write("```" + (node.attrs.language || "") + "\n");
      state.text(node.textContent, false);
      state.ensureNewLine();
      state.write("```");
      state.closeBlock(node);
    },
    bulletList(state, node) {
      state.renderList(node, "  ", () => "- ");
    },
    orderedList(state, node) {
      const start = Number(node.attrs.start ?? 1) || 1;
      const maxWidth = String(start + node.childCount - 1).length;
      state.renderList(node, " ".repeat(maxWidth + 2), (i) => {
        const label = String(start + i);
        return label + ". " + " ".repeat(maxWidth - label.length);
      });
    },
    listItem(state, node) {
      state.renderContent(node);
    },
    table: tableToMarkdown,
    // Rows and cells are never reached: `table` renders its own subtree. Present because
    // `MarkdownSerializer` refuses to serialise a document containing a node it has no entry
    // for, and "no entry" would fail at the moment someone sends a table rather than at build.
    tableRow: () => {},
    tableHeader: () => {},
    tableCell: () => {},
  },
  {
    bold: { open: "**", close: "**", mixable: true, expelEnclosingWhitespace: true },
    italic: { open: "*", close: "*", mixable: true, expelEnclosingWhitespace: true },
    strike: { open: "~~", close: "~~", mixable: true, expelEnclosingWhitespace: true },
    code: defaultMarkdownSerializer.marks.code,
    link: defaultMarkdownSerializer.marks.link,
  },
);

/** The document as markdown — the value the store holds and he eventually reads. */
export function toMarkdown(doc: ProseNode): string {
  return serializer.serialize(doc, { tightLists: true }).replace(/\s+$/, "");
}

/**
 * How many characters of markdown precede a document position.
 *
 * The slash menu is driven by `(text, cursorPosition)` where the cursor is an index into the
 * plain string — a rich editor has a tree and no such index, so it is computed: cut the document
 * at the caret and serialise what is left of it. Uses the same serialiser as `toMarkdown`, which
 * is the only reason the two numbers describe the same string.
 */
export function markdownOffset(doc: ProseNode, position: number): number {
  // Clamped rather than trusted. The caret is reported by the editor and the document is read
  // here, and between the two sits a transaction that may already have shortened it — an
  // out-of-range cut throws inside ProseMirror, which would take the composer down over a
  // position that was merely stale.
  const at = Math.max(0, Math.min(position, doc.content.size));
  return toMarkdown(doc.cut(0, at)).length;
}

// --------------------------------------------------------------------------- //
// Markdown → document
// --------------------------------------------------------------------------- //

const md = MarkdownIt("commonmark", { html: false })
  .enable(["table", "strikethrough"])
  // Deliberately off. A bare URL in a message is usually a path or an id being quoted, and
  // turning it into a link on the way in means the text he receives grew angle brackets that
  // nobody typed.
  .disable(["linkify"]);

type Frame = { type: string; attrs?: Record<string, unknown>; content: ProseNode[] };

/** Markdown as a document. Never throws: unparseable input becomes a paragraph of itself, because
 *  losing what someone typed is not an acceptable answer to a malformed table. */
export function fromMarkdown(text: string): ProseNode {
  if (!text.trim()) return composerSchema.topNodeType.createAndFill()!;
  try {
    return build(md.parse(text, {}));
  } catch {
    return paragraphsOf(text);
  }
}

function paragraphsOf(text: string): ProseNode {
  const paragraphs = text.split(/\n{2,}/).map((block) =>
    composerSchema.nodes.paragraph.create(null, block ? composerSchema.text(block) : null),
  );
  return composerSchema.topNodeType.create(null, paragraphs);
}

function build(tokens: Token[]): ProseNode {
  const stack: Frame[] = [{ type: "doc", content: [] }];
  const top = () => stack[stack.length - 1];

  const open = (type: string, attrs?: Record<string, unknown>) => stack.push({ type, attrs, content: [] });
  const close = () => {
    const frame = stack.pop()!;
    const nodeType = composerSchema.nodes[frame.type];
    // A cell holds blocks; markdown hands its contents over as an inline run. Wrapping it here is
    // the one place the two shapes disagree, and the reason this walker exists at all.
    const content =
      (frame.type === "tableCell" || frame.type === "tableHeader") && !isBlockRun(frame.content)
        ? [composerSchema.nodes.paragraph.create(null, frame.content)]
        : frame.content;
    const node = nodeType.createAndFill(frame.attrs ?? null, content);
    if (node) top().content.push(node);
  };

  for (const token of tokens) {
    switch (token.type) {
      case "paragraph_open":
        open("paragraph");
        break;
      case "heading_open":
        open("heading", { level: Math.min(3, Number(token.tag.slice(1)) || 1) });
        break;
      case "blockquote_open":
        open("blockquote");
        break;
      case "bullet_list_open":
        open("bulletList");
        break;
      case "ordered_list_open":
        open("orderedList", { start: Number(token.attrGet("start") ?? 1) || 1 });
        break;
      case "list_item_open":
        open("listItem");
        break;
      case "table_open":
        open("table");
        break;
      case "tr_open":
        open("tableRow");
        break;
      case "th_open":
        open("tableHeader");
        break;
      case "td_open":
        open("tableCell");
        break;
      case "paragraph_close":
      case "heading_close":
      case "blockquote_close":
      case "bullet_list_close":
      case "ordered_list_close":
      case "list_item_close":
      case "table_close":
      case "tr_close":
      case "th_close":
      case "td_close":
        close();
        break;
      // `thead` and `tbody` have no counterpart in the schema — a Tiptap table is rows all the
      // way down, and which of them is the header is told by the cell type.
      case "thead_open":
      case "thead_close":
      case "tbody_open":
      case "tbody_close":
        break;
      case "fence":
      case "code_block":
        top().content.push(
          composerSchema.nodes.codeBlock.create(
            { language: (token.info || "").trim().split(/\s+/)[0] || null },
            token.content.replace(/\n$/, "") ? composerSchema.text(token.content.replace(/\n$/, "")) : null,
          ),
        );
        break;
      case "hr":
        top().content.push(composerSchema.nodes.horizontalRule.create());
        break;
      case "inline":
        top().content.push(...inlineNodes(token.children ?? []));
        break;
      default:
        break;
    }
  }

  const doc = stack[0];
  return composerSchema.topNodeType.createAndFill(null, doc.content) ?? paragraphsOf("");
}

function isBlockRun(nodes: ProseNode[]): boolean {
  return nodes.length > 0 && nodes.every((node) => node.isBlock);
}

function inlineNodes(tokens: Token[]): ProseNode[] {
  const out: ProseNode[] = [];
  const marks: { type: string; attrs?: Record<string, unknown> }[] = [];
  const applied = () => marks.map((m) => composerSchema.marks[m.type].create(m.attrs ?? null));

  for (const token of tokens) {
    switch (token.type) {
      case "text":
        if (token.content) out.push(composerSchema.text(token.content, applied()));
        break;
      case "code_inline":
        out.push(composerSchema.text(token.content, [...applied(), composerSchema.marks.code.create()]));
        break;
      case "strong_open":
        marks.push({ type: "bold" });
        break;
      case "em_open":
        marks.push({ type: "italic" });
        break;
      case "s_open":
        marks.push({ type: "strike" });
        break;
      case "link_open":
        marks.push({ type: "link", attrs: { href: token.attrGet("href") ?? "" } });
        break;
      case "strong_close":
      case "em_close":
      case "s_close":
      case "link_close":
        marks.pop();
        break;
      case "hardbreak":
        out.push(composerSchema.nodes.hardBreak.create());
        break;
      // A single newline inside a paragraph. Markdown's own reading of it is "a space", but this
      // is a composer: someone who pressed Enter to start a new line is looking at two lines and
      // expects to keep looking at two lines. Kept as a break, which also round-trips — the
      // serialiser writes it back as a hard break rather than collapsing it.
      case "softbreak":
        out.push(composerSchema.nodes.hardBreak.create());
        break;
      case "image":
        out.push(
          composerSchema.text(`![${token.content}](${token.attrGet("src") ?? ""})`, applied()),
        );
        break;
      default:
        if (token.content) out.push(composerSchema.text(token.content, applied()));
        break;
    }
  }
  return out;
}
