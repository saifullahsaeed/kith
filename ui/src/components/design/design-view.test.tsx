/**
 * What actually reaches the DOM once a design is rendered in the app's own document.
 *
 * `parse.test.ts` asserts what survives the whitelist. This asserts the other end of the same
 * question — that what survived arrives intact, and that the things which did *not* survive are
 * absent from the real DOM rather than merely absent from the tree. They are separate tests
 * because a renderer can reintroduce what a parser removed, and `dangerouslySetInnerHTML` is one
 * plausible edit away at every point in this file.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DesignView } from "@/components/design/design-view";

describe("rendering a design", () => {
  it("puts his markup on the screen", () => {
    render(<DesignView html='<div data-k="hero"><p data-k="hero.title">Ship it</p></div>' />);
    expect(screen.getByText("Ship it")).toBeInTheDocument();
  });

  it("carries the ids a patch will address", () => {
    const { container } = render(<DesignView html='<p data-k="hero.title">Ship it</p>' />);
    expect(container.querySelector('[data-k="hero.title"]')).not.toBeNull();
  });

  it("applies the inline styles that survived", () => {
    const { container } = render(<DesignView html='<div data-k="a" style="display:flex;gap:12px"></div>' />);
    const node = container.querySelector('[data-k="a"]') as HTMLElement;
    expect(node.style.display).toBe("flex");
    expect(node.style.gap).toBe("12px");
  });

  it("keeps a token reference intact, so the design follows the app's theme", () => {
    const { container } = render(<DesignView html='<div data-k="a" style="color:var(--kith-accent)"></div>' />);
    const node = container.querySelector('[data-k="a"]') as HTMLElement;
    expect(node.getAttribute("style")).toContain("var(--kith-accent)");
  });

  it("renders svg as svg rather than as unknown elements", () => {
    const { container } = render(
      <DesignView html='<svg data-k="icon" viewBox="0 0 10 10"><circle data-k="dot" r="4"/></svg>' />,
    );
    const svg = container.querySelector('[data-k="icon"]')!;
    expect(svg.namespaceURI).toBe("http://www.w3.org/2000/svg");
    expect(svg.getAttribute("viewBox")).toBe("0 0 10 10");
  });

  it("defines the tokens the dialect is written in", () => {
    // Without these, a design saying `var(--kith-accent)` refers to a variable that exists only
    // inside the sealed frame, and every colour in it comes out as the initial value.
    const { container } = render(<DesignView html='<div data-k="a"></div>' />);
    const root = container.firstElementChild as HTMLElement;
    expect(root.style.getPropertyValue("--kith-accent")).toBeTruthy();
    expect(root.style.getPropertyValue("--kith-text")).toBeTruthy();
  });

  it("scopes his stylesheet so it cannot restyle the app around it", () => {
    const { container } = render(<DesignView html="<style>.card{gap:4px}</style><div data-k=a class=card></div>" />);
    const sheet = container.querySelector("style")!.textContent ?? "";
    expect(sheet).toMatch(/\.design-[\w-]+ \.card/);
    expect(sheet).not.toMatch(/^\s*\.card/);
  });
});

describe("what does not reach the DOM", () => {
  it("never mounts a script, whatever the tree said", () => {
    const { container } = render(<DesignView html='<div data-k="a"><script>steal()</script></div>' />);
    expect(container.querySelector("script")).toBeNull();
    expect(container.innerHTML).not.toContain("steal");
  });

  it("never mounts a handler attribute", () => {
    const { container } = render(<DesignView html='<div data-k="a" onclick="go()">hi</div>' />);
    expect(container.innerHTML).not.toContain("go()");
    expect(container.querySelector('[data-k="a"]')!.getAttribute("onclick")).toBeNull();
  });

  it("never mounts a url that would fetch", () => {
    const { container } = render(
      <DesignView html='<img data-k="a" src="https://evil.example/pixel.png">' />,
    );
    expect(container.querySelector('[data-k="a"]')!.getAttribute("src")).toBeNull();
  });
});

describe("selection, which is the editor's foothold", () => {
  it("reports the innermost node under the pointer", async () => {
    const onSelect = vi.fn();
    render(
      <DesignView
        html='<div data-k="outer"><p data-k="inner">Ship it</p></div>'
        onSelect={onSelect}
      />,
    );
    await userEvent.click(screen.getByText("Ship it"));
    // A design tool selects what you pointed at, not the frame it happens to live in.
    expect(onSelect).toHaveBeenCalledWith("inner");
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it("clears the selection when you click the ground", async () => {
    const onSelect = vi.fn();
    const { container } = render(<DesignView html='<p data-k="inner">Ship it</p>' onSelect={onSelect} />);
    await userEvent.click(container.firstElementChild as HTMLElement);
    expect(onSelect).toHaveBeenCalledWith(null);
  });

  it("marks the current node so the editor can draw on it", () => {
    const { container } = render(<DesignView html='<p data-k="inner">hi</p>' selected="inner" />);
    expect(container.querySelector('[data-k="inner"]')!.getAttribute("data-selected")).toBe("true");
  });
});

describe("dragging, which reorders rather than moves", () => {
  const transfer = () => ({ setData: vi.fn(), effectAllowed: "" });

  it("reports an insertion point rather than a position", async () => {
    const onMove = vi.fn();
    const { container } = render(
      <DesignView
        html='<div data-k="row"><p data-k="a">A</p><p data-k="b">B</p></div>'
        onMove={onMove}
      />,
    );

    const b = container.querySelector('[data-k="b"]')!;
    fireEvent.dragStart(b, { dataTransfer: transfer() });
    fireEvent.dragOver(container.querySelector('[data-k="a"]')!, { dataTransfer: transfer() });
    fireEvent.drop(container.querySelector('[data-k="a"]')!, { dataTransfer: transfer() });

    // Where it goes in the list, and whose child it becomes — not an x and a y, because the
    // dialect has no way to write one down.
    expect(onMove).toHaveBeenCalledWith("b", "row", 0);
  });

  it("refuses to drop a node into its own descendant", () => {
    const onMove = vi.fn();
    const { container } = render(
      <DesignView html='<div data-k="outer"><p data-k="inner">hi</p></div>' onMove={onMove} />,
    );

    fireEvent.dragStart(container.querySelector('[data-k="outer"]')!, { dataTransfer: transfer() });
    fireEvent.dragOver(container.querySelector('[data-k="inner"]')!, { dataTransfer: transfer() });
    fireEvent.drop(container.querySelector('[data-k="inner"]')!, { dataTransfer: transfer() });

    expect(onMove).not.toHaveBeenCalled();
  });

  it("marks the edge the drop would land against", () => {
    const { container } = render(
      <DesignView html='<div data-k="row"><p data-k="a">A</p><p data-k="b">B</p></div>' onMove={vi.fn()} />,
    );
    fireEvent.dragStart(container.querySelector('[data-k="b"]')!, { dataTransfer: transfer() });
    fireEvent.dragOver(container.querySelector('[data-k="a"]')!, { dataTransfer: transfer() });
    expect(container.querySelector('[data-k="a"]')!.getAttribute("data-drop")).toBeTruthy();
  });

  it("is not draggable at all when the view is read-only", () => {
    const { container } = render(<DesignView html='<p data-k="a">A</p>' />);
    expect(container.querySelector('[data-k="a"]')!.getAttribute("draggable")).toBeNull();
  });
});
