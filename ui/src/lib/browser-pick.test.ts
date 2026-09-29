import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown from "react-markdown";
import { describe, expect, it } from "vitest";

import { noteFor } from "./browser-pick";
import { MARKDOWN_PLUGINS, MARKDOWN_REHYPE } from "./markdown-plugins";

/**
 * The crosshair's caption is page text sent as the person's own message.
 *
 * Every part of it but the size came from the page being picked — the selector, the label, the
 * address — and it is rendered through the same markdown as anything the person types. So the
 * assertion that matters is about the rendered message, not the string: a label the page wrote as
 * a link is not a link, and a selector with a backtick in it stays inside its code span.
 */
const rendered = (markdown: string): string =>
  renderToStaticMarkup(
    createElement(ReactMarkdown, { remarkPlugins: MARKDOWN_PLUGINS, rehypePlugins: MARKDOWN_REHYPE }, markdown),
  );

describe("the caption a pick puts in the composer", () => {
  it("does not turn a label the page wrote as markdown into a link", () => {
    const html = rendered(
      noteFor({ selector: "button.cta", label: "[Open ticket](https://evil.example) **now**", width: 80, height: 20 }),
    );
    expect(html).not.toContain("evil.example\"");
    expect(html).not.toContain("<a ");
    expect(html).not.toContain("<strong>");
    expect(html).toContain("<code>[Open ticket](https://evil.example) **now**</code>");
  });

  it("keeps a selector with backticks in it inside one code span", () => {
    const selector = 'div[data-testid="a`b``c"] > span';
    const html = rendered(noteFor({ selector, label: "Card" }));
    expect(html.match(/<code>/g)).toHaveLength(2); // the selector, and the label
    expect(html).toContain(`<code>${selector.replace(/"/g, "&quot;").replace(/>/g, "&gt;")}</code>`);
  });

  it("still links the page it came from", () => {
    const html = rendered(noteFor({ selector: "#go", url: "https://example.com/a_b*c" }));
    expect(html).toContain('href="https://example.com/a_b*c"');
  });
});
