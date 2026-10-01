/**
 * Upload on the Files screen, as a person does it: pick files with the button or drop them on the
 * list or on a folder in it, watch each one arrive, and answer "replace or keep both" when a name
 * is taken. The server is a stand-in `XMLHttpRequest`; the listing is a mocked `fetch`.
 */
import { act, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/ui/confirm";
import { render } from "@/test/render";
import { WorkspaceFiles } from "./files";

class FakeXHR {
  static sent: FakeXHR[] = [];
  upload: { onprogress: ((event: { loaded: number; total: number; lengthComputable: boolean }) => void) | null } = {
    onprogress: null,
  };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  status = 0;
  responseText = "";
  responseType = "";
  body: FormData | null = null;
  open() {}
  setRequestHeader() {}
  send(body: FormData) {
    this.body = body;
    FakeXHR.sent.push(this);
  }
  abort() {
    this.onabort?.();
  }
  respond(status: number, body: unknown) {
    this.status = status;
    this.responseText = JSON.stringify(body);
    this.onload?.();
  }
  get folder() {
    return this.body?.get("folder");
  }
  get name() {
    return (this.body?.get("file") as File | null)?.name;
  }
}

const LISTING = {
  ".": [
    { name: "src", type: "dir", size: 0, modified: 1790761163 },
    { name: "notes.txt", type: "file", size: 12, modified: 1790761163 },
  ],
};

beforeEach(() => {
  FakeXHR.sent = [];
  vi.stubGlobal("XMLHttpRequest", FakeXHR);
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = new URL(String(input), "http://kith.test");
    if (url.pathname === "/api/workspace") {
      const path = url.searchParams.get("path") ?? ".";
      const entries = LISTING[path as keyof typeof LISTING] ?? [];
      return Promise.resolve(new Response(JSON.stringify({ path, entries }), { status: 200 }));
    }
    return Promise.resolve(new Response("{}", { status: 404 }));
  });
});
afterEach(() => vi.unstubAllGlobals());

async function screenWithListing() {
  render(
    <ConfirmProvider>
      <WorkspaceFiles />
    </ConfirmProvider>,
  );
  await screen.findByRole("option", { name: /notes\.txt/ });
}

const file = (name: string, text = "hello") => new File([text], name, { type: "text/plain" });
const saved = (path: string, size = 5) => ({ ok: true, path, name: path.split("/").pop(), type: "file", size, modified: 1790761200 });

/** Let the queue's pump run after a state change. */
const settle = () => act(async () => {});

describe("Upload on the Files screen", () => {
  it("has the board's Upload button and drop target, naming the folder they put files in", async () => {
    await screenWithListing();
    expect(screen.getByRole("button", { name: "Upload" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Drop files here to put them in his folder/ })).toBeInTheDocument();
  });

  it("sends what the picker chose, one at a time, with progress on each", async () => {
    await screenWithListing();
    fireEvent.change(screen.getByLabelText(/Upload files to/), { target: { files: [file("a.txt"), file("b.txt")] } });
    await settle();
    // One request at a time: the second waits for the first.
    expect(FakeXHR.sent).toHaveLength(1);
    expect([FakeXHR.sent[0].folder, FakeXHR.sent[0].name]).toEqual([".", "a.txt"]);
    const tray = screen.getByRole("list", { name: "Uploads" });
    expect(within(tray).getByText(/Waiting/)).toBeInTheDocument();

    act(() => FakeXHR.sent[0].upload.onprogress?.({ loaded: 2, total: 4, lengthComputable: true }));
    expect(within(tray).getByRole("progressbar", { name: "Uploading a.txt" })).toHaveAttribute("aria-valuenow", "50");
    expect(within(tray).getByText("50%")).toBeInTheDocument();

    act(() => FakeXHR.sent[0].respond(200, saved("a.txt")));
    await settle();
    expect(FakeXHR.sent).toHaveLength(2);
    expect(FakeXHR.sent[1].name).toBe("b.txt");
    expect(within(tray).getByLabelText("Uploaded")).toBeInTheDocument();
  });

  it("asks when a name is taken, and keep both saves it as name (2)", async () => {
    await screenWithListing();
    fireEvent.change(screen.getByLabelText(/Upload files to/), { target: { files: [file("notes.txt")] } });
    await settle();
    act(() =>
      FakeXHR.sent[0].respond(409, {
        error: "notes.txt is already in this folder",
        existing: { path: "notes.txt", name: "notes.txt", type: "file", size: 12, modified: 1790761163 },
      }),
    );
    await settle();
    const tray = screen.getByRole("list", { name: "Uploads" });
    expect(within(tray).getByText(/Already in his folder — 12 B/)).toBeInTheDocument();
    fireEvent.click(within(tray).getByRole("button", { name: "Keep both" }));
    await settle();
    expect(FakeXHR.sent).toHaveLength(2);
    expect(FakeXHR.sent[1].name).toBe("notes (2).txt");
    expect(FakeXHR.sent[1].body?.has("overwrite")).toBe(false);
  });

  it("replace sends it again over the one that is there", async () => {
    await screenWithListing();
    fireEvent.change(screen.getByLabelText(/Upload files to/), { target: { files: [file("notes.txt")] } });
    await settle();
    act(() =>
      FakeXHR.sent[0].respond(409, {
        error: "taken",
        existing: { path: "notes.txt", name: "notes.txt", type: "file", size: 12, modified: 1790761163 },
      }),
    );
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Replace" }));
    await settle();
    expect(FakeXHR.sent[1].name).toBe("notes.txt");
    expect(FakeXHR.sent[1].body?.get("overwrite")).toBe("1");
  });

  it("says a refusal on the file's own row", async () => {
    await screenWithListing();
    fireEvent.change(screen.getByLabelText(/Upload files to/), { target: { files: [file("x.txt")] } });
    await settle();
    act(() => FakeXHR.sent[0].respond(403, { error: "that folder is outside his workspace" }));
    await settle();
    expect(within(screen.getByRole("list", { name: "Uploads" })).getByText("that folder is outside his workspace")).toBeInTheDocument();
  });

  it("drops onto a folder row into that folder, and onto the list into the open one", async () => {
    await screenWithListing();
    const section = screen.getByRole("region", { name: "Files in his folder" });
    const transfer = (files: File[]) => ({ types: ["Files"], files, items: [], dropEffect: "none" });

    const folderRow = screen.getByRole("option", { name: /src/ });
    fireEvent.dragEnter(folderRow, { dataTransfer: transfer([]) });
    expect(screen.getByRole("button", { name: /Drop files here to put them in src/ })).toBeInTheDocument();
    fireEvent.drop(folderRow, { dataTransfer: transfer([file("into-src.txt")]) });
    await settle();
    expect([FakeXHR.sent[0].folder, FakeXHR.sent[0].name]).toEqual(["src", "into-src.txt"]);
    act(() => FakeXHR.sent[0].respond(200, saved("src/into-src.txt")));
    await settle();

    fireEvent.drop(section, { dataTransfer: transfer([file("here.txt")]) });
    await settle();
    expect([FakeXHR.sent[1].folder, FakeXHR.sent[1].name]).toEqual([".", "here.txt"]);
  });

  it("ignores a drag that is not files from the machine", async () => {
    await screenWithListing();
    const section = screen.getByRole("region", { name: "Files in his folder" });
    fireEvent.drop(section, { dataTransfer: { types: ["text/plain"], files: [], items: [] } });
    await settle();
    expect(FakeXHR.sent).toHaveLength(0);
    expect(screen.queryByRole("list", { name: "Uploads" })).toBeNull();
  });
});
