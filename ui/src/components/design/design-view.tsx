/**
 * A design, on the screen, in the app's own document — and the dragging that goes with it.
 *
 * This is the component the seal was traded away for. A sealed frame (a plugin surface) has an
 * opaque origin and is safe without reading anything inside it; a design has to be measurable and
 * selectable, which means being in the same document as the editor, which means everything it
 * contains has already been through `parse.ts`. Nothing here is a boundary — by the time a node
 * reaches this file it is a whitelisted tag with whitelisted attributes and whitelisted CSS.
 *
 * ── Why a drag reorders instead of moving ───────────────────────────────────────────────────
 *
 * There is nowhere for a design to be moved *to*. The dialect has no coordinates in it — a
 * layout is a flex direction, a gap and a padding — so "drag this 40px left" has no
 * representation, and an editor offering it would have to answer by writing `position:absolute`,
 * which is exactly the thing that makes a design uneditable afterwards and unwritable by him in
 * the first place.
 *
 * So dragging expresses what a flex layout actually has: order, and which frame something is in.
 * It is the same decision Figma made when auto-layout arrived, for the same reason, and it is why
 * the drop target here is an insertion point between two children rather than a position.
 */
import { createElement, type DragEvent, type ReactNode, useId, useMemo, useState } from "react";

import { frameTokens } from "@/lib/seal";
import { type DesignNode, parseDesign } from "@/lib/design/parse";
import { paletteFor } from "@/lib/kith-palette";
import { assetPaths, useAssets } from "@/lib/design/assets";
import { type System, tokensFor } from "@/lib/design/system";
import { useDesignSystem } from "@/lib/design/use-system";
import { useDarkMode } from "@/lib/theme";
import { attrsToProps, elementName, scopeStyleSheet, styleToReact, VOID_TAGS } from "@/lib/design/render";

export interface DesignViewProps {
  /** The file's contents, exactly as it is on disk. */
  html: string;
  /** The node the editor considers current, by `data-k`. */
  selected?: string | null;
  onSelect?: (id: string | null) => void;
  /** Given, the design becomes draggable. The index is a position among the target's children
   *  *after* the moved node has been taken out, which is what `moveNode` expects. */
  onMove?: (id: string, parentId: string, index: number) => void;
  /** The system this design is drawn in. Resolved here when not given, so a caller that does not
   *  care — a thumbnail, say — gets the right palette anyway. */
  system?: System;
  /** Which project the design's own assets and system resolve against. */
  projectId?: number | null;
  className?: string;
}

/** Where a dragged node would land. */
interface Drop {
  parent: string;
  index: number;
  /** The node the line is drawn against, and which edge of it it goes on.
   *
   *  An edge rather than "before"/"after" because the indicator is drawn in CSS, and CSS cannot
   *  know whether this row is a row. The axis is decided here, where the parent's computed
   *  direction is in hand, so a column gets a horizontal line and a row a vertical one. */
  anchor: string;
  side: "top" | "bottom" | "left" | "right";
}

/** Is `id` inside `ancestor`? A node dropped into its own descendant is a document with no tree. */
function contains(node: DesignNode, ancestorId: string, id: string): boolean {
  const find = (current: DesignNode): DesignNode | null => {
    if (current.id === ancestorId) return current;
    for (const child of current.children) {
      const hit = find(child);
      if (hit) return hit;
    }
    return null;
  };
  const ancestor = find(node);
  if (!ancestor) return false;
  const walk = (current: DesignNode): boolean =>
    current.id === id || current.children.some(walk);
  return ancestor.children.some(walk);
}

export function DesignView({ html, selected, onSelect, onMove, system, projectId = null, className }: DesignViewProps) {
  const parsed = useMemo(() => parseDesign(html), [html]);
  const [dragging, setDragging] = useState<string | null>(null);
  const [drop, setDrop] = useState<Drop | null>(null);

  // `useId` is stable across renders and unique per mounted view, so two designs open at once
  // cannot restyle each other. Punctuation out, because the result is going into a selector.
  const scope = `design-${useId().replace(/[^\w-]/g, "")}`;
  /* A document's defaults first, then his sheet.
   *
   * Everything in here inherits from the app, which in dark mode means a pale `color` and the
   * app's own font — so any colour a design leaves unnamed, or names and fails to resolve,
   * rendered pale-on-white and read as blank. The checker opens the same file in a plain
   * Chromium and sees black on white; the two renderers disagreeing is the whole class of bug
   * this surface keeps producing. Literal rather than themed for that reason: the artboard is
   * a sheet of paper, and it does not change colour because the room did.
   *
   * In the sheet under `:where()` rather than inline, because inline would beat the design's
   * own `body { background: var(--paper) }` — zero specificity loses to every rule he wrote. */
  const sheet = useMemo(
    () =>
      [
        `:where(.${scope}) { color: #000; background: #fff; color-scheme: light; ` +
          `font-family: system-ui, -apple-system, "Segoe UI", sans-serif; font-size: 16px; ` +
          `font-weight: 400; line-height: normal; letter-spacing: normal; text-align: left; }`,
        scopeStyleSheet(parsed.sheet, `.${scope}`),
      ]
        .filter(Boolean)
        .join("\n"),
    [parsed.sheet, scope],
  );

  /* The tokens the dialect is written in, defined where the design can see them.
   *
   * A sealed frame gets these in its own stylesheet, which is why a plugin surface can say
   * `var(--kith-accent)` and mean something. A design has no document of its own — it is in
   * the app's — and the app's variables are named `--background`, `--kith` and so on. So a
   * design written in the dialect would have referred to nine variables that resolve to nothing,
   * and every colour in it would have come out as the initial value. Same `frameTokens` both
   * ends, for the reason `seal.ts` gives: the last time these two were written
   * separately, three of the nine silently failed to travel and it read as "light mode doesn't
   * work". */
  /* Every picture the design asks for, fetched rather than linked — see `assets.ts` for why an
   * `<img src="logo.png">` would otherwise be handed the app's own index.html with a 200. */
  const wanted = useMemo(() => {
    const found: string[] = [];
    const walk = (node: DesignNode) => {
      const src = node.attrs.src;
      if (typeof src === "string") found.push(src);
      node.children.forEach(walk);
    };
    parsed.root.children.forEach(walk);
    return assetPaths(found);
  }, [parsed]);
  const assets = useAssets(wanted, projectId);

  const dark = useDarkMode();
  const resolved = useDesignSystem(projectId);
  const inUse = system ?? resolved;

  const tokens = useMemo(() => {
    const style: Record<string, string> = {};
    /* Kith's nine, still, and unconditionally.
     *
     * Every design written before systems existed refers to `--kith-accent` and friends, and a
     * surface that stopped defining them would break all of it at once for nothing. They cost
     * nine declarations. `frameTokens` is the sealed frame's contract and is read here for the
     * same reason `seal.ts` gives — the last time these names were written out twice,
     * three of the nine silently failed to travel and it read as "light mode doesn't work". */
    for (const [name, value] of Object.entries(frameTokens(paletteFor(dark)))) {
      style[`--kith-${name}`] = value;
    }
    // And the system's own, namespaced so it can never reach a variable the editor draws with.
    Object.assign(style, tokensFor(inUse));
    return style;
  }, [dark, inUse]);

  const land = () => {
    if (onMove && dragging && drop) {
      // The index the pointer produced counts the dragged node, because it is still in the list
      // you are pointing at. `moveNode` counts the list without it. Moving something downwards
      // past its own old position is therefore one too far, and this is the off-by-one that
      // reads as "dragging works, except downwards".
      const siblings = childrenOf(parsed.root, drop.parent);
      const from = siblings.indexOf(dragging);
      const index = from >= 0 && from < drop.index ? drop.index - 1 : drop.index;
      onMove(dragging, drop.parent, index);
    }
    setDragging(null);
    setDrop(null);
  };

  const render = (node: DesignNode, parentId: string, index: number): ReactNode => {
    const attrs = { ...attrsToProps(node.attrs) };
    if (typeof attrs.src === "string" && !attrs.src.startsWith("data:")) {
      // Resolved, or nothing at all: a src pointing at a workspace path the app cannot serve is
      // a broken box with the whole application inside it.
      const url = assets[attrs.src];
      if (url) attrs.src = url;
      else delete attrs.src;
    }

    const props: Record<string, unknown> = {
      key: node.id,
      // The same id the patch language uses, in the DOM, so the editor can go from a click to a
      // node to an edit without a second map that could disagree with this one.
      "data-k": node.id,
      ...attrs,
      style: styleToReact(node.style),
    };

    if (node.classes.length) props.className = node.classes.join(" ");
    if (selected === node.id) props["data-selected"] = "true";
    if (drop?.anchor === node.id) props["data-drop"] = drop.side;
    if (dragging === node.id) props["data-dragging"] = "true";

    if (onSelect) {
      props.onClick = (event: React.MouseEvent) => {
        // Innermost wins: a click lands on the deepest node under the pointer, which is what
        // selecting in a design tool means. Without this, every click selects the outermost frame.
        event.stopPropagation();
        onSelect(node.id);
      };
    }

    if (onMove) {
      props.draggable = true;
      props.onDragStart = (event: DragEvent) => {
        event.stopPropagation();
        // Firefox will not start a drag without this, and the payload is unused: the id lives in
        // state, because `dataTransfer` cannot be read during `dragover`, which is where the
        // insertion point has to be decided.
        event.dataTransfer.setData("text/plain", node.id);
        event.dataTransfer.effectAllowed = "move";
        setDragging(node.id);
      };
      props.onDragEnd = () => {
        setDragging(null);
        setDrop(null);
      };
      props.onDragOver = (event: DragEvent) => {
        if (!dragging || dragging === node.id) return;
        if (contains(parsed.root, dragging, node.id)) return;
        event.preventDefault();
        event.stopPropagation();

        const box = (event.currentTarget as HTMLElement).getBoundingClientRect();
        // Which axis the *parent* lays its children out on decides which half of this node the
        // pointer is in. Using the node's own direction would put a horizontal insertion line in
        // a column, which reads as the editor not knowing where things go.
        const parentEl = (event.currentTarget as HTMLElement).parentElement;
        const column =
          !parentEl || !getComputedStyle(parentEl).flexDirection.startsWith("row");
        const past = column
          ? event.clientY > box.top + box.height / 2
          : event.clientX > box.left + box.width / 2;

        setDrop({
          parent: parentId,
          index: past ? index + 1 : index,
          anchor: node.id,
          side: column ? (past ? "bottom" : "top") : past ? "right" : "left",
        });
      };
      props.onDrop = (event: DragEvent) => {
        event.preventDefault();
        event.stopPropagation();
        land();
      };
    }

    if (VOID_TAGS.has(node.tag)) return createElement(elementName(node.tag), props);

    const children: ReactNode[] = [];
    if (node.segments) {
      // In the file's order, so the space in "Arrived <strong>9:31</strong>" survives.
      for (const one of node.segments) {
        children.push(typeof one === "string" ? one : render(node.children[one], node.id, one));
      }
    } else {
      if (node.text) children.push(node.text);
      for (const [at, child] of node.children.entries()) children.push(render(child, node.id, at));
    }

    return createElement(elementName(node.tag), props, children.length ? children : undefined);
  };

  return (
    <div
      className={`${scope}${className ? ` ${className}` : ""}`}
      /* The design's own root declarations last, so they win.
       *
       * `<body style="--mk-bg:#05080a; background:var(--mk-bg); color:var(--mk-ink)">` is where
       * a whole-document design puts the values it invented, and until `parseDesign` started
       * reading it those values did not exist by the time anything referenced them. They go
       * after the palette and the system because they are the most specific thing the author
       * said, and they cannot reach `--kith-*`, which `safeDeclaration` still refuses. */
      /* The declared frame is the design's own size. Without it every design was laid out at
       * whatever width its container happened to be, so a phone screen was measured as a
       * desktop and the columns that should have stacked did not. */
      /* An artboard, not a column of HTML.
       *
       * This was `minHeight`, which let the design grow to whatever the panel was and stop
       * wherever the content did — so a 390x844 phone rendered as a 390-wide strip running the
       * full height of the window, and nothing on screen said what size the screen was. A
       * design file is a fixed rectangle; that is most of what makes one feel like a design
       * file rather than a web page in a narrow frame.
       *
       * `auto` rather than `hidden` because this is also the editor: a design that overruns its
       * frame has to stay reachable, and the checker already reports the real height. */
      style={{
        width: parsed.frame.width,
        height: parsed.frame.height,
        overflow: "auto",
        ...tokens,
        ...styleToReact(parsed.root.style),
      }}
      onClick={onSelect ? () => onSelect(null) : undefined}
      onDragOver={onMove ? (event) => event.preventDefault() : undefined}
      onDrop={onMove ? land : undefined}
    >
      {sheet ? <style>{sheet}</style> : null}
      {parsed.root.children.map((child, at) => render(child, parsed.root.id, at))}
    </div>
  );
}

/** The direct children of one node, by id. */
function childrenOf(root: DesignNode, id: string): string[] {
  const find = (node: DesignNode): DesignNode | null => {
    if (node.id === id) return node;
    for (const child of node.children) {
      const hit = find(child);
      if (hit) return hit;
    }
    return null;
  };
  return find(root)?.children.map((child) => child.id) ?? [];
}
