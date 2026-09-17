/**
 * The editor as a loop: open a file, change something, write it back.
 *
 * Two of these are about the loop working. The third is about the failure that would matter most
 * — he is an agent who writes to his own files unprompted, so an editor that held a copy of a
 * file for ten minutes and then wrote it back would be a way to silently delete a turn's work.
 * That is `two-writers-one-worktree` with a save button on it, and the answer here is the honest
 * one: re-read before writing, refuse when it moved.
 */
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DesignPage } from "@/components/design/design-page";
import { fetchWorkspaceFile, saveWorkspaceFile } from "@/lib/backend/brain";
import { render, screen, waitFor } from "@/test/render";

vi.mock("@/lib/backend/brain", () => ({
  fetchWorkspaceFile: vi.fn(),
  saveWorkspaceFile: vi.fn(),
}));

const read = vi.mocked(fetchWorkspaceFile);
const write = vi.mocked(saveWorkspaceFile);

const DESIGN = '<div data-k="hero" style="display:flex; gap:12px"><p data-k="hero.title">Ship it</p></div>';

function open(content = DESIGN) {
  read.mockResolvedValue({ path: "screens/home.html", content });
  write.mockResolvedValue({ path: "screens/home.html" });
  return render(
    <DesignPage path="screens/home.html" projectId={null} title="Home" onBack={() => {}} />,
  );
}

beforeEach(() => {
  read.mockReset();
  write.mockReset();
});

describe("editing a design", () => {
  it("shows the file once it is read", async () => {
    open();
    expect(await screen.findByText("Ship it")).toBeInTheDocument();
  });

  it("has nothing to save until something changes", async () => {
    open();
    await screen.findByText("Ship it");
    expect(screen.getByRole("button", { name: /no changes/i })).toBeDisabled();
  });

  it("writes the edit into the document and saves exactly that", async () => {
    open();
    await userEvent.click(await screen.findByText("Ship it"));

    // A bare number is a length, because that is what anyone types.
    await userEvent.type(screen.getByLabelText("Padding"), "24");
    await userEvent.tab();

    await userEvent.click(await screen.findByRole("button", { name: /^save$/i }));
    await waitFor(() => expect(write).toHaveBeenCalled());

    const written = write.mock.calls[0][1];
    expect(written).toContain("padding: 24px");
    // And the rest of his file is still his file.
    expect(written).toContain("Ship it");
    expect(written).toContain('data-k="hero"');
  });

  it("does not rewrite a design that was only looked at", async () => {
    open();
    await userEvent.click(await screen.findByText("Ship it"));
    expect(screen.getByRole("button", { name: /no changes/i })).toBeDisabled();
    expect(write).not.toHaveBeenCalled();
  });
});

describe("the write that must not clobber him", () => {
  it("refuses to save over a file that moved underneath it", async () => {
    open();
    await userEvent.click(await screen.findByText("Ship it"));
    await userEvent.type(screen.getByLabelText("Padding"), "24");
    await userEvent.tab();

    // He wrote to the file while it was open in the editor.
    read.mockResolvedValue({ path: "screens/home.html", content: "<p data-k=x>his newer version</p>" });

    await userEvent.click(await screen.findByRole("button", { name: /^save$/i }));
    expect(await screen.findByText(/changed on disk/i)).toBeInTheDocument();
    expect(write).not.toHaveBeenCalled();
  });
});
