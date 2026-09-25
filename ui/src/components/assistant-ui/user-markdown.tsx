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
import { splitWorkflowPrefix } from "@/lib/workflow-prefix";

export const UserMarkdownText: FC = () => {
  const said = useAuiState((s) => (s.part.type === "text" ? s.part.text : ""));
  if (!said) return null;
  // A message that opted into a workflow says so as a chip, not as a word that reads like a typo.
  const { workflow, rest: text } = splitWorkflowPrefix(said);
  return (
    <div className="kith-user-markdown">
      {workflow ? (
        <span
          className="mb-1 inline-flex items-center rounded bg-teal-400/15 px-1.5 py-px font-mono text-[10.5px] text-teal-700 dark:text-teal-300"
          title="Sent as a workflow: he may split this into agents that run in the background"
        >
          /workflow
        </span>
      ) : null}
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
