/**
 * A design, on the screen, in the app's own document.
 *
 * This is the component the seal was traded away for. `html-canvas.tsx` mounts a frame with an
 * opaque origin and is safe without reading anything inside it; a design has to be measurable and
 * selectable, which means being in the same document as the editor, which means everything it
 * contains has already been through `parse.ts`. Nothing here is a boundary — by the time a node
 * reaches this file it is a whitelisted tag with whitelisted attributes and whitelisted CSS, and
 * if that is not true the mistake is upstream.
 *
 * Read-only for now, deliberately. Selection is here because it costs one handler and proves the
 * ids the editor will address nodes by are the ones actually in the DOM; dragging, handles and
 * the inspector come next and do not change anything below.
 */
import { createElement, type ReactNode, useId, useMemo } from "react";

import { type DesignNode, parseDesign } from "@/lib/design/parse";
import { attrsToProps, elementName, scopeStyleSheet, styleToReact, VOID_TAGS } from "@/lib/design/render";

export interface DesignViewProps {
  /** The file's contents, exactly as it is on disk. */
  html: string;
  /** The node the editor considers current, by `data-k`. */
  selected?: string | null;
  onSelect?: (id: string | null) => void;
  className?: string;
}

function renderNode(
  node: DesignNode,
  selected: string | null | undefined,
  onSelect: ((id: string | null) => void) | undefined,
): ReactNode {
  const props: Record<string, unknown> = {
    key: node.id,
    // The same id the patch language uses, in the DOM, so the editor can go from a click to a
    // node to an edit without a second map that could disagree with this one.
    "data-k": node.id,
    ...attrsToProps(node.attrs),
    style: styleToReact(node.style),
  };

  if (node.classes.length) props.className = node.classes.join(" ");
  if (selected === node.id) props["data-selected"] = "true";
  if (onSelect) {
    props.onClick = (event: React.MouseEvent) => {
      // Innermost wins: a click lands on the deepest node under the pointer, which is what
      // selecting in a design tool means. Without this, every click selects the outermost frame.
      event.stopPropagation();
      onSelect(node.id);
    };
  }

  if (VOID_TAGS.has(node.tag)) return createElement(elementName(node.tag), props);

  const children: ReactNode[] = [];
  if (node.text) children.push(node.text);
  for (const child of node.children) children.push(renderNode(child, selected, onSelect));

  return createElement(elementName(node.tag), props, children.length ? children : undefined);
}

export function DesignView({ html, selected, onSelect, className }: DesignViewProps) {
  const parsed = useMemo(() => parseDesign(html), [html]);

  // `useId` is stable across renders and unique per mounted view, so two designs open at once
  // cannot restyle each other. Punctuation out, because the result is going into a selector.
  const scope = `design-${useId().replace(/[^\w-]/g, "")}`;
  const sheet = useMemo(() => scopeStyleSheet(parsed.sheet, `.${scope}`), [parsed.sheet, scope]);

  return (
    <div
      className={`${scope}${className ? ` ${className}` : ""}`}
      onClick={onSelect ? () => onSelect(null) : undefined}
    >
      {sheet ? <style>{sheet}</style> : null}
      {parsed.root.children.map((child) => renderNode(child, selected, onSelect))}
    </div>
  );
}
