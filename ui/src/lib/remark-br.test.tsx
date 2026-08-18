import { render, screen } from "@testing-library/react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { describe, expect, it } from "vitest";
import { remarkBr } from "@/lib/remark-br";

const TABLE = `
| Companies | Why |
| --- | --- |
| **Cursor (Anysphere)**<br>**Cognition (Devin)**<br>**Codeium** | AST repo-mapping |
`;

describe("a <br> in a table cell", () => {
  it("was rendered as literal text before", () => {
    render(<ReactMarkdown remarkPlugins={[remarkGfm]}>{TABLE}</ReactMarkdown>);
    expect(screen.getByRole("cell", { name: /Cursor/ }).textContent).toContain("<br>");
  });

  it("becomes a real line break", () => {
    render(<ReactMarkdown remarkPlugins={[remarkGfm, remarkBr]}>{TABLE}</ReactMarkdown>);
    const cell = screen.getByRole("cell", { name: /Cursor/ });
    expect(cell.textContent).not.toContain("<br>");
    expect(cell.querySelectorAll("br")).toHaveLength(2);
  });

  it("leaves other html alone, so nothing new is rendered", () => {
    render(
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkBr]}>
        {`a <img src=x onerror=alert(1)> b`}
      </ReactMarkdown>,
    );
    expect(document.querySelector("img")).toBeNull();
  });
});
