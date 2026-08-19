"use client";

/**
 * What you wrote, rendered.
 *
 * A user message used to be printed verbatim, which was fine while the composer could only make
 * one kind of thing. Now that it can make lists and tables, printing the source means the message
 * you sent looks nothing like the message you composed — pipes and hashes where a moment ago
 * there was a table — and the round trip is the whole promise of a rich composer.
 *
 * Deliberately not `MarkdownText`, which is the renderer for his replies: that one carries syntax
 * highlighting, mermaid diagrams and copy buttons, all of which are right for a long answer you
 * are reading and wrong for a bubble you wrote thirty seconds ago. This is the same markdown at a
 * smaller weight, inheriting the bubble's own colour.
 */

import type { FC } from "react";
import ReactMarkdown from "react-markdown";
import { useAuiState } from "@assistant-ui/react";
import { MARKDOWN_PLUGINS, MARKDOWN_REHYPE } from "@/lib/markdown-plugins";

export const UserMarkdownText: FC = () => {
  const text = useAuiState((s) => (s.part.type === "text" ? s.part.text : ""));
  if (!text) return null;
  return (
    <div className="kith-user-markdown">
      <ReactMarkdown
        remarkPlugins={MARKDOWN_PLUGINS} rehypePlugins={MARKDOWN_REHYPE}
        components={{
          // Links open outward; everything else is styled from `index.css`, where the rules can
          // sit next to the composer's own and be kept honest against them.
          a: ({ node: _node, ...props }) => (
            <a {...props} target="_blank" rel="noreferrer noopener" />
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
};
