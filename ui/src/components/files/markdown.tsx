import { memo } from "react";
import type { ComponentPropsWithoutRef } from "react";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";

import { HtmlCanvas } from "@/components/assistant-ui/html-canvas";
import { MermaidDiagram } from "@/components/assistant-ui/mermaid-diagram";
import { linkTarget, useFileViewer } from "@/lib/files";
import { CodeBlock } from "./code-block";
import { MARKDOWN_PLUGINS, MARKDOWN_REHYPE } from "@/lib/markdown-plugins";

/* ── Markdown renderer (react-markdown + gfm + highlighted code) ─────────── */

export const Markdown = memo(function Markdown({ children }: { children: string }) {
  return (
    <div className="text-sm leading-relaxed break-words">
      <ReactMarkdown remarkPlugins={MARKDOWN_PLUGINS} rehypePlugins={MARKDOWN_REHYPE} components={MD}>
        {children}
      </ReactMarkdown>
    </div>
  );
});

/** Markdown on one line.
 *
 * The block renderer wraps everything in `<p>` with margins, which in a timeline row
 * or beside a timestamp pushes the rest of the line away and breaks the layout. This
 * keeps emphasis, code and links — the things he actually uses in a short line — and
 * renders paragraphs as spans.
 *
 * Headings and lists are deliberately flattened rather than honoured: a heading inside
 * a one-line summary is a mistake in the text, not something to lay out.
 */
export const MarkdownInline = memo(function MarkdownInline({ children }: { children: string }) {
  return (
    <ReactMarkdown remarkPlugins={MARKDOWN_PLUGINS} rehypePlugins={MARKDOWN_REHYPE} components={MD_INLINE}>
      {children}
    </ReactMarkdown>
  );
});

/**
 * A link in a document he wrote, which is usually a link to another thing he wrote.
 *
 * Same decision as the chat renderer's anchor, and here it matters more: a plan or a report
 * is mostly cross-references, and every one of them was an `<a href>` pointing at a relative
 * or absolute path. The browser resolved those against this origin and the app's catch-all
 * answered with the app — so clicking a link in his own document opened a second Kith. See
 * `linkTarget` in `lib/files.ts` for the whole chain.
 */
function Anchor({
  href,
  className,
  children,
  ...rest
}: ComponentPropsWithoutRef<"a"> & { className: string }) {
  const openFile = useFileViewer((state) => state.open);
  const target = href ? linkTarget(href) : null;
  if (target?.kind === "file") {
    return (
      <button
        type="button"
        onClick={() => openFile(target.path)}
        title={`Open ${target.path}`}
        className={className}
      >
        {children}
      </button>
    );
  }
  if (target?.kind === "anchor") {
    return (
      <a className={className} href={href} {...rest}>
        {children}
      </a>
    );
  }
  return (
    <a className={className} href={href} target="_blank" rel="noreferrer" {...rest}>
      {children}
    </a>
  );
}

const MD_INLINE: Components = {
  p: (p) => <span {...p} />,
  h1: (p) => <span className="font-semibold" {...p} />,
  h2: (p) => <span className="font-semibold" {...p} />,
  h3: (p) => <span className="font-semibold" {...p} />,
  ul: (p) => <span {...p} />,
  ol: (p) => <span {...p} />,
  li: (p) => <span className="before:content-['·_']" {...p} />,
  strong: (p) => <strong className="font-semibold" {...p} />,
  em: (p) => <em className="italic" {...p} />,
  code: (p) => <code className="bg-muted rounded px-1 py-0.5 font-mono text-[0.9em]" {...p} />,
  pre: (p) => <span {...p} />,
  a: (p) => <Anchor className="text-kith underline decoration-dotted" {...p} />,
  blockquote: (p) => <span className="text-muted-foreground italic" {...p} />,
  hr: () => null,
  br: () => <> </>,
};

const MD: Components = {
  h1: (p) => <h1 className="mt-7 mb-3 text-xl font-semibold tracking-tight first:mt-0" {...p} />,
  h2: (p) => (
    <h2
      className="mt-7 mb-2.5 border-b border-border/50 pb-1.5 text-lg font-semibold tracking-tight first:mt-0"
      {...p}
    />
  ),
  h3: (p) => <h3 className="mt-5 mb-2 text-base font-semibold first:mt-0" {...p} />,
  h4: (p) => <h4 className="mt-4 mb-1.5 text-sm font-semibold first:mt-0" {...p} />,
  h5: (p) => <h5 className="mt-3 mb-1 text-sm font-semibold first:mt-0" {...p} />,
  h6: (p) => (
    <h6
      className="mt-3 mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground first:mt-0"
      {...p}
    />
  ),
  p: (p) => <p className="my-3 leading-relaxed first:mt-0 last:mb-0" {...p} />,
  a: (p) => (
    <Anchor className="text-kith hover:text-kith/80 underline underline-offset-2" {...p} />
  ),
  ul: (p) => <ul className="my-3 ms-5 list-disc space-y-1 marker:text-muted-foreground" {...p} />,
  ol: (p) => (
    <ol className="my-3 ms-5 list-decimal space-y-1 marker:text-muted-foreground" {...p} />
  ),
  li: (p) => <li className="leading-relaxed" {...p} />,
  blockquote: (p) => (
    <blockquote
      className="my-3 border-s-2 border-kith/40 ps-4 text-muted-foreground italic"
      {...p}
    />
  ),
  hr: () => <hr className="my-6 border-border" />,
  strong: (p) => <strong className="font-semibold" {...p} />,
  em: (p) => <em className="italic" {...p} />,
  img: ({ alt, ...p }) => (
    <img alt={alt} className="my-3 max-w-full rounded-lg border border-border/60" {...p} />
  ),
  // Shared with chat and your bubbles — see `.kith-table` in index.css.
  table: (p) => (
    <div className="kith-table">
      <table {...p} />
    </div>
  ),
  pre: ({ children }) => <>{children}</>, // CodeBlock renders its own <pre>
  code: ({
    className,
    children,
    ...props
  }: ComponentPropsWithoutRef<"code"> & { className?: string }) => {
    const text = String(children ?? "").replace(/\n$/, "");
    const match = /language-([\w-]+)/.exec(className ?? "");
    const isBlock = !!match || text.includes("\n");
    if (!isBlock) {
      return (
        <code className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-[0.85em]" {...props}>
          {children}
        </code>
      );
    }
    const lang = match?.[1];
    const source = (
      <div className="my-4 overflow-hidden rounded-xl border border-border/60 bg-muted/15">
        <CodeBlock code={text} language={lang} label={lang} />
      </div>
    );
    // The same drawing chat gets. He writes design notes and plans as markdown files, and a
    // diagram that renders in the conversation but not in the document it was written into is
    // the sort of inconsistency you notice immediately and cannot explain.
    if (lang === "mermaid") return <MermaidDiagram code={text} fallback={source} />;
    if (lang === "html") return <HtmlCanvas code={text} fallback={source} />;
    return source;
  },
};
