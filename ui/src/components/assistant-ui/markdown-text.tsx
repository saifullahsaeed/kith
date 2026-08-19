"use client";

import "@assistant-ui/react-markdown/styles/dot.css";

import {
  type CodeHeaderProps,
  MarkdownTextPrimitive,
  unstable_memoizeMarkdownComponents as memoizeMarkdownComponents,
  useIsMarkdownCodeBlock,
} from "@assistant-ui/react-markdown";
import { MARKDOWN_PLUGINS, MARKDOWN_REHYPE } from "@/lib/markdown-plugins";
import { type FC, memo, useState } from "react";
import { CheckIcon, CopyIcon } from "lucide-react";

import { MermaidBlock } from "@/components/assistant-ui/mermaid-diagram";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { copyText, linkTarget, looksLikeHisFile, useFileViewer } from "@/lib/files";
import { grammarFor, highlight } from "@/lib/highlight";
import { cn } from "@/lib/utils";

/** A ```mermaid fence is a picture, not a listing — see `mermaid-diagram.tsx`.
 *
 *  `CodeHeader: null` goes with it. The header is the bar that says "mermaid" with a copy
 *  button, which is right above a block of source and wrong above a drawing: it labels the
 *  picture with the name of the language it is not showing you. Copy lives on the diagram's
 *  own hover toolbar instead, next to the control for making it bigger. */
const byLanguage = {
  mermaid: { SyntaxHighlighter: MermaidBlock, CodeHeader: () => null },
};

const MarkdownTextImpl = () => {
  return (
    <MarkdownTextPrimitive
      remarkPlugins={MARKDOWN_PLUGINS} rehypePlugins={MARKDOWN_REHYPE}
      className="aui-md"
      components={defaultComponents}
      componentsByLanguage={byLanguage}
      defer
    />
  );
};

export const MarkdownText = memo(MarkdownTextImpl);

/**
 * The bar above a fenced block: what language it is, and a button to take it.
 *
 * The copy button did nothing at all — no clipboard write, no tick, no error in the console.
 * It went through assistant-ui's `navigator.clipboard.writeText` with the rejection handler
 * written as `() => {}`, so the one thing that reliably happens in an Electron webview —
 * `writeText` rejecting with `NotAllowedError` — was caught and dropped on the floor. Confirmed
 * by driving the real page: the write rejects, the icon never swaps, and nothing is logged.
 *
 * `copyText` is the path the rest of the app already copies through. It tries the async
 * clipboard, falls back to `execCommand` on a different permission route, and returns whether
 * either actually worked. The identical fix is described at length above `CopyReply` in
 * `thread.tsx` — it was made for the reply button and never reached this one.
 *
 * The tick is shown only on a `true`. A checkmark over an empty clipboard is worse than a button
 * that visibly fails, because you only find out later, from the thing you pasted.
 */
const CodeHeader: FC<CodeHeaderProps> = ({ language, code }) => {
  const [isCopied, setIsCopied] = useState(false);
  const onCopy = () => {
    if (!code || isCopied) return;
    void copyText(code).then((ok) => {
      if (!ok) return;
      setIsCopied(true);
      setTimeout(() => setIsCopied(false), 2000);
    });
  };

  return (
    <div className="aui-code-header-root border-border/50 bg-muted/50 mt-3 flex items-center justify-between rounded-t-xl border border-b-0 px-3.5 py-1.5 text-xs">
      <span className="aui-code-header-language text-muted-foreground font-medium lowercase">
        {language}
      </span>
      <TooltipIconButton tooltip="Copy" onClick={onCopy}>
        {!isCopied && <CopyIcon className="animate-in zoom-in-75 fade-in duration-150" />}
        {isCopied && <CheckIcon className="animate-in zoom-in-50 fade-in duration-200 ease-out" />}
      </TooltipIconButton>
    </div>
  );
};


const defaultComponents = memoizeMarkdownComponents({
  h1: ({ className, ...props }) => (
    <h1
      className={cn(
        "aui-md-h1 mt-5 mb-2 scroll-m-20 text-xl font-semibold first:mt-0 last:mb-0",
        className,
      )}
      {...props}
    />
  ),
  h2: ({ className, ...props }) => (
    <h2
      className={cn(
        "aui-md-h2 mt-5 mb-2 scroll-m-20 text-lg font-semibold first:mt-0 last:mb-0",
        className,
      )}
      {...props}
    />
  ),
  h3: ({ className, ...props }) => (
    <h3
      className={cn(
        "aui-md-h3 mt-4 mb-1.5 scroll-m-20 text-base font-semibold first:mt-0 last:mb-0",
        className,
      )}
      {...props}
    />
  ),
  h4: ({ className, ...props }) => (
    <h4
      className={cn(
        "aui-md-h4 mt-3.5 mb-1 scroll-m-20 text-base font-medium first:mt-0 last:mb-0",
        className,
      )}
      {...props}
    />
  ),
  h5: ({ className, ...props }) => (
    <h5
      className={cn("aui-md-h5 mt-3 mb-1 text-sm font-semibold first:mt-0 last:mb-0", className)}
      {...props}
    />
  ),
  h6: ({ className, ...props }) => (
    <h6
      className={cn("aui-md-h6 mt-3 mb-1 text-sm font-medium first:mt-0 last:mb-0", className)}
      {...props}
    />
  ),
  p: ({ className, ...props }) => (
    <p className={cn("aui-md-p my-3 leading-relaxed first:mt-0 last:mb-0", className)} {...props} />
  ),
  a: function Anchor({ className, href, children, ...props }) {
    const openFile = useFileViewer((state) => state.open);
    const style = cn(
      "aui-md-a text-primary hover:text-primary/80 underline underline-offset-2",
      className,
    );

    // A link to something he wrote opens it here, rather than being handed to the browser.
    //
    // It was an ordinary anchor, so `[diagram-test.mmd](diagram-test.mmd)` — which is how he
    // hands over a file he just made — rendered as a link to a relative URL. Electron routes
    // navigation out to the default browser, so the best case was the system opening a path
    // that means nothing outside this app, and the usual case was a click that did nothing at
    // all. The viewer this opens is the same one an inline `path` mention already used; only
    // the anchor was missing it.
    //
    // Which shapes count is `linkTarget`'s call now, and it decides the opposite way round:
    // the browser gets a link only when it is really a web address. Recognising *file* shapes
    // and defaulting the rest to `<a>` left every unrecognised one — an absolute path, a
    // `file:` URL — falling through to a browser that resolves it against this origin and
    // serves it the app. See `lib/files.ts`.
    const target = href ? linkTarget(href) : null;
    if (target?.kind === "file") {
      return (
        <button
          type="button"
          onClick={() => openFile(target.path)}
          title={`Open ${target.path}`}
          className={style}
        >
          {children}
        </button>
      );
    }
    // A same-page jump stays in the page: `target="_blank"` on one opens a second copy of the
    // app scrolled to a heading.
    if (target?.kind === "anchor") {
      return (
        <a className={style} href={href} {...props}>
          {children}
        </a>
      );
    }
    // A real address. `noreferrer` because these are ones he found, not ones the person
    // chose to visit.
    return (
      <a className={style} href={href} target="_blank" rel="noreferrer" {...props}>
        {children}
      </a>
    );
  },
  blockquote: ({ className, ...props }) => (
    <blockquote
      className={cn(
        "aui-md-blockquote border-muted-foreground/30 text-muted-foreground my-3 border-s-2 ps-4",
        className,
      )}
      {...props}
    />
  ),
  ul: ({ className, ...props }) => (
    <ul
      className={cn(
        "aui-md-ul marker:text-muted-foreground my-3 ms-5 list-disc [&>li]:mt-1",
        className,
      )}
      {...props}
    />
  ),
  ol: ({ className, ...props }) => (
    <ol
      className={cn(
        "aui-md-ol marker:text-muted-foreground my-3 ms-5 list-decimal [&>li]:mt-1",
        className,
      )}
      {...props}
    />
  ),
  hr: ({ className, ...props }) => (
    <hr className={cn("aui-md-hr border-muted-foreground/20 my-3", className)} {...props} />
  ),
  // The look lives in `.kith-table` in index.css, shared with the file viewer and your own
  // bubbles. What was here was assistant-ui's stock styling, whose `w-full` is why a wide
  // table crushed its columns instead of scrolling.
  table: ({ className, ...props }) => (
    <div className="kith-table">
      <table className={cn("aui-md-table", className)} {...props} />
    </div>
  ),
  th: ({ className, ...props }) => <th className={cn("aui-md-th", className)} {...props} />,
  td: ({ className, ...props }) => <td className={cn("aui-md-td", className)} {...props} />,
  tr: ({ className, ...props }) => <tr className={cn("aui-md-tr", className)} {...props} />,
  li: ({ className, ...props }) => (
    <li className={cn("aui-md-li leading-relaxed", className)} {...props} />
  ),
  strong: ({ className, ...props }) => (
    <strong className={cn("aui-md-strong font-semibold", className)} {...props} />
  ),
  sup: ({ className, ...props }) => (
    <sup className={cn("aui-md-sup [&>a]:text-xs [&>a]:no-underline", className)} {...props} />
  ),
  pre: ({ className, ...props }) => (
    <pre
      className={cn(
        "aui-md-pre border-border/50 bg-muted/30 overflow-x-auto rounded-t-none rounded-b-xl border border-t-0 p-3.5 text-[13px] leading-relaxed",
        className,
      )}
      {...props}
    />
  ),
  code: function Code({ className, children, ...props }) {
    const isCodeBlock = useIsMarkdownCodeBlock();
    const openFile = useFileViewer((state) => state.open);
    const text = typeof children === "string" ? children : "";

    // A path he wrote in backticks becomes something you can click. Only inline code,
    // and only a recognised file shape — see lib/files.ts for why scanning prose or
    // fenced blocks would be worse than not doing this at all.
    if (!isCodeBlock && looksLikeHisFile(text)) {
      return (
        <button
          type="button"
          onClick={() => openFile(text)}
          title={`Open ${text.trim()}`}
          className="aui-md-file-mention bg-kith-soft text-kith decoration-kith/40 hover:decoration-kith cursor-pointer rounded-md px-1.5 py-0.5 font-mono text-[0.85em] underline decoration-dotted underline-offset-2"
        >
          {children}
        </button>
      );
    }

    // A fenced block, coloured. The file viewer has coloured code since it was written; a reply
    // containing the same JSON rendered as flat grey. `language-<name>` is what the fence's info
    // string becomes.
    //
    // Only a language hljs actually knows. Guessing is not the fallback here — a chat block
    // re-renders on every token, and detection both costs 24x more and changes its mind as the
    // block grows, so an unlabelled block stays plain rather than flickering. See `grammarFor`.
    const grammar = isCodeBlock ? grammarFor(/language-([\w-]+)/.exec(className ?? "")?.[1]) : null;
    if (grammar && text) {
      return (
        <code
          className={cn("hljs font-mono", className)}
          {...props}
          dangerouslySetInnerHTML={{ __html: highlight(text, grammar) }}
        />
      );
    }

    return (
      <code
        className={cn(
          !isCodeBlock &&
            "aui-md-inline-code bg-muted rounded-md px-1.5 py-0.5 font-mono text-[0.85em]",
          className,
        )}
        {...props}
      >
        {children}
      </code>
    );
  },
  CodeHeader,
});
