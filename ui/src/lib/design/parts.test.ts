/**
 * A `<k-use>` in the editor's tree: what the person clicks, and where an edit to it lands.
 *
 * The text-level expansion is pinned in `compose.test.ts`. This is the other half — the tree
 * the editor selects in and patches through — and the property that matters most is the one
 * that is invisible when it breaks: ids. The screen's ids are written into the screen file by
 * scanning its tags in order, a part's ids into the part's file the same way. If expanding a
 * part shifted the screen's numbering by one, every edit after the first `<k-use>` would land on
 * the node before the one selected, with nothing on screen to say so.
 */
import { describe, expect, it } from "vitest";

import type { Parts } from "@/lib/design/compose";
import { applyStyle, detachInstance, materialiseIds } from "@/lib/design/edit";
import { type DesignNode, parseDesign } from "@/lib/design/parse";

const ROW = '<div data-k="row" style="display: flex; gap: 12px"><strong>{{label}}</strong><span>{{value}}</span></div>';
const PANEL = '<section style="display: flex; flex-direction: column; gap: 8px"><h2>{{title}}</h2><k-slot></k-slot></section>';
const parts: Parts = {
  "row.detail": { html: ROW, path: "/p/.kith/design/components/row.detail.html" },
  panel: { html: PANEL },
};

function flatten(node: DesignNode): DesignNode[] {
  return [node, ...node.children.flatMap(flatten)];
}

function find(node: DesignNode, id: string): DesignNode {
  const hit = flatten(node).find((one) => one.id === id);
  if (!hit) throw new Error(`no node ${id}`);
  return hit;
}

describe("an instance in the tree", () => {
  const screen = '<main><k-use name="row.detail" label="Checking" value="$3,240" style="padding: 4px"></k-use><p>after</p></main>';

  it("is the part's own root, carrying the use's id and the use's style over the part's", () => {
    const { root } = parseDesign(screen, parts);
    const [main] = root.children;
    const [row] = main.children;
    expect(row.id).toBe("k2");
    expect(row.instance).toMatchObject({ name: "row.detail", props: { label: "Checking", value: "$3,240" } });
    expect(row.instance?.path).toBe(parts["row.detail"].path);
    expect(row.style).toMatchObject({ display: "flex", gap: "12px", padding: "4px" });
  });

  it("fills the holes", () => {
    const { root } = parseDesign(screen, parts);
    const texts = flatten(root).map((one) => one.text).filter(Boolean);
    expect(texts).toEqual(expect.arrayContaining(["Checking", "$3,240", "after"]));
  });

  it("numbers the screen exactly as if the part were not expanded", () => {
    const expanded = flatten(parseDesign(screen, parts).root).filter((one) => !one.part).map((one) => one.id);
    const plain = flatten(parseDesign(screen).root).map((one) => one.id);
    expect(expanded).toEqual(plain);
  });

  it("numbers the part's nodes the way its own file would be numbered", () => {
    const inside = flatten(parseDesign(screen, parts).root).filter((one) => one.part);
    const alone = flatten(parseDesign(ROW).root).slice(1).map((one) => one.id);
    expect(inside.map((one) => one.part?.local)).toEqual(alone.slice(1));
    expect(new Set(inside.map((one) => one.id)).size).toBe(inside.length);
  });

  it("does not report a use's props as attributes the whitelist dropped", () => {
    expect(parseDesign(screen, parts).dropped).toEqual([]);
  });

  it("is a holder with only the slot in it when there is no inventory — the shape edits count in", () => {
    const [main] = parseDesign(screen).root.children;
    expect(main.children[0]).toMatchObject({ id: "k2", style: { display: "contents" }, children: [] });
  });
});

describe("what a prop can carry", () => {
  it("is text: markup in a prop is escaped, never parsed into nodes", () => {
    const { root } = parseDesign('<k-use name="row.detail" label="<img src=x onerror=alert(1)>"></k-use>', parts);
    expect(flatten(root).some((one) => one.tag === "img")).toBe(false);
    expect(flatten(root).some((one) => one.text?.includes("<img"))).toBe(true);
  });
});

describe("a slot", () => {
  it("holds the screen's own nodes, with the screen's ids, inside the part", () => {
    const screen = '<k-use name="panel" title="Coming up"><p data-k="rent">Rent</p><k-use name="row.detail" label="Internet"></k-use></k-use>';
    const { root } = parseDesign(screen, parts);
    const rent = find(root, "rent");
    expect(rent.part).toBeUndefined();
    const panel = root.children[0];
    expect(panel.instance?.name).toBe("panel");
    expect(flatten(panel).some((one) => one.id === "rent")).toBe(true);
    expect(flatten(panel).filter((one) => one.instance?.name === "row.detail")).toHaveLength(1);
  });

  it("shows where content goes when the part is opened on its own", () => {
    const { root } = parseDesign(PANEL);
    expect(flatten(root).some((one) => one.text?.startsWith("Slot"))).toBe(true);
  });
});

describe("a part the system does not have", () => {
  it("is drawn and labelled rather than vanishing, and is reported", () => {
    const { root, dropped, uses } = parseDesign('<k-use name="nope"><b>kept</b></k-use>', parts);
    expect(root.children[0].instance).toMatchObject({ name: "nope", missing: true });
    expect(root.children[0].text).toBe("Missing component: nope");
    expect(dropped.join(" ")).toContain("nope");
    expect(uses).toEqual(["nope"]);
  });

  it("stops a part that uses itself", () => {
    const { dropped } = parseDesign('<k-use name="loop"></k-use>', { loop: { html: '<div><k-use name="loop"></k-use></div>' } });
    expect(dropped.join(" ")).toContain("uses itself");
  });
});

describe("self-closed uses", () => {
  it("are siblings, not each nested in the one before", () => {
    const { root } = parseDesign('<div><k-use name="row.detail" /><k-use name="row.detail" /><p>end</p></div>', parts);
    expect(root.children[0].children.map((one) => one.instance?.name ?? one.tag)).toEqual(["row.detail", "row.detail", "p"]);
  });
});

describe("editing", () => {
  const screen = '<main><k-use name="row.detail" label="Checking"></k-use><p>after</p></main>';

  it("writes an id onto the <k-use> like any other tag", () => {
    expect(materialiseIds(screen)).toBe('<main data-k="k1"><k-use data-k="k2" name="row.detail" label="Checking"></k-use><p data-k="k3">after</p></main>');
  });

  it("puts a change to the instance on its own tag — this screen only", () => {
    const changed = applyStyle(materialiseIds(screen), "k2", { padding: "8px" });
    expect(changed).toContain('<k-use data-k="k2" name="row.detail" label="Checking" style="padding: 8px">');
    const row = parseDesign(changed, parts).root.children[0].children[0];
    expect(row.style.padding).toBe("8px");
  });

  it("detaches one instance into plain markup, keeping its id and renaming the part's", () => {
    const detached = detachInstance(screen, "k2", parts);
    expect(detached).not.toContain("<k-use");
    expect(detached).toBe(
      '<main data-k="k1"><div data-k="k2" style="display: flex; gap: 12px" data-k-component="row.detail">' +
        '<strong>Checking</strong><span></span></div><p data-k="k3">after</p></main>',
    );
    const node = find(parseDesign(detached, parts).root, "k2");
    expect(node.instance).toBeUndefined();
    expect(node.component).toBe("row.detail");
  });
});
