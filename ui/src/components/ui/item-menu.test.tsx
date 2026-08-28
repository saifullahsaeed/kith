import { describe, expect, it, vi } from "vitest";

import { ItemMenu } from "@/components/ui/item-menu";
import { render, screen, fireEvent } from "@/test/render";

vi.mock("@/lib/files", () => ({ copyText: vi.fn(async () => true) }));
const { copyText } = await import("@/lib/files");

/** Right-click, which is the only way into any of this. */
function openOn(label: string) {
  fireEvent.contextMenu(screen.getByText(label));
}

/**
 * Copying the thing, and copying its name for the thing.
 *
 * A conversation's id is the name of its transcript file, what the API addresses it by, and the
 * only way to point Kith at one particular afternoon. It was reachable by opening the folder and
 * reading a filename, and no other way.
 */
describe("taking something away from a row", () => {
  it("offers the id beside the text, not among the actions", async () => {
    render(
      <ItemMenu title="A chat" copy="A chat" copyId="20260829-020612-5ec563">
        <span>row</span>
      </ItemMenu>,
    );
    openOn("row");

    expect(await screen.findByText("Copy id")).toBeTruthy();
    expect(screen.getByText("Copy text")).toBeTruthy();
    // The id is its own hint, because it is short enough to read and seeing it is often the
    // whole reason for reaching for this.
    expect(screen.getByText("20260829-020612-5ec563")).toBeTruthy();
  });

  it("puts the id on the clipboard, not the title", async () => {
    render(
      <ItemMenu title="A chat" copy="A chat" copyId="an-id">
        <span>row</span>
      </ItemMenu>,
    );
    openOn("row");
    fireEvent.click(await screen.findByText("Copy id"));
    expect(copyText).toHaveBeenCalledWith("an-id");
  });

  it("leaves the row out entirely for a thing with no id", async () => {
    render(
      <ItemMenu title="A note" copy="A note">
        <span>row</span>
      </ItemMenu>,
    );
    openOn("row");
    await screen.findByText("Copy text");
    expect(screen.queryByText("Copy id")).toBeNull();
  });

  it("still opens for something that has only an id", async () => {
    // `hasBody` decides whether there is a menu at all, and it counted `copy` and the actions.
    // A row with an id and nothing else would have right-clicked into nothing.
    render(
      <ItemMenu title="A thing" copyId="just-an-id">
        <span>row</span>
      </ItemMenu>,
    );
    openOn("row");
    expect(await screen.findByText("Copy id")).toBeTruthy();
  });
});
