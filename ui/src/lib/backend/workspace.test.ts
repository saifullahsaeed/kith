/**
 * The two workspace calls that are not plain JSON round trips: search, which must go quiet on an
 * older server rather than break the palette, and upload, which is an `XMLHttpRequest` for its
 * progress and so has to do by hand what `fetch` gets from the app's wrappers.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  SEARCH_UNSUPPORTED,
  UploadCancelled,
  UploadConflict,
  keepBothName,
  searchWorkspace,
  uploadWorkspaceFile,
} from "./workspace";

/** A stand-in `XMLHttpRequest` that records what was sent and answers when told to. */
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
  method = "";
  url = "";
  headers: Record<string, string> = {};
  body: FormData | null = null;
  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }
  setRequestHeader(name: string, value: string) {
    this.headers[name] = value;
  }
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
}

beforeEach(() => {
  FakeXHR.sent = [];
  vi.stubGlobal("XMLHttpRequest", FakeXHR);
});
afterEach(() => {
  vi.unstubAllGlobals();
  delete window.__kithToken;
});

const file = (name = "notes.txt", text = "hello") => new File([text], name, { type: "text/plain" });

describe("uploadWorkspaceFile", () => {
  it("posts the file and its folder as a form, and resolves with what landed", async () => {
    const done = uploadWorkspaceFile(file(), "src/assets");
    const xhr = FakeXHR.sent[0];
    expect([xhr.method, xhr.url]).toEqual(["POST", "/api/workspace/upload"]);
    expect(xhr.body?.get("folder")).toBe("src/assets");
    expect((xhr.body?.get("file") as File | undefined)?.name).toBe("notes.txt");
    expect(xhr.body?.has("overwrite")).toBe(false);
    xhr.respond(200, { ok: true, path: "src/assets/notes.txt", name: "notes.txt", type: "file", size: 5, modified: 1 });
    await expect(done).resolves.toMatchObject({ path: "src/assets/notes.txt", size: 5 });
  });

  it("reports progress as it goes", async () => {
    const seen: [number, number][] = [];
    const done = uploadWorkspaceFile(file(), ".", { onProgress: (sent, total) => seen.push([sent, total]) });
    const xhr = FakeXHR.sent[0];
    xhr.upload.onprogress?.({ loaded: 2, total: 5, lengthComputable: true });
    xhr.upload.onprogress?.({ loaded: 5, total: 5, lengthComputable: true });
    xhr.respond(200, { path: "notes.txt", name: "notes.txt", type: "file", size: 5, modified: 1 });
    await done;
    expect(seen).toEqual([
      [2, 5],
      [5, 5],
    ]);
  });

  it("turns a 409 into a conflict carrying the file that is there", async () => {
    const done = uploadWorkspaceFile(file(), ".");
    FakeXHR.sent[0].respond(409, {
      error: "notes.txt is already in this folder",
      existing: { path: "notes.txt", name: "notes.txt", type: "file", size: 99, modified: 7 },
    });
    const error = await done.catch((e: unknown) => e);
    expect(error).toBeInstanceOf(UploadConflict);
    expect((error as UploadConflict).existing.size).toBe(99);
  });

  it("sends overwrite and a chosen name when asked to", async () => {
    void uploadWorkspaceFile(file(), ".", { overwrite: true, name: "notes (2).txt" }).catch(() => {});
    const xhr = FakeXHR.sent[0];
    expect(xhr.body?.get("overwrite")).toBe("1");
    expect((xhr.body?.get("file") as File | undefined)?.name).toBe("notes (2).txt");
  });

  it("says what the server said when it refuses", async () => {
    const done = uploadWorkspaceFile(file(), "../");
    FakeXHR.sent[0].respond(403, { error: "that folder is outside his workspace" });
    await expect(done).rejects.toThrow("that folder is outside his workspace");
  });

  it("says an older server can't take uploads rather than a bare 404", async () => {
    const done = uploadWorkspaceFile(file(), ".");
    FakeXHR.sent[0].respond(404, { error: "not found" });
    await expect(done).rejects.toThrow(/can't take uploads yet/);
  });

  it("is cancelled by its signal, as a cancel and not a failure", async () => {
    const stop = new AbortController();
    const done = uploadWorkspaceFile(file(), ".", { signal: stop.signal });
    stop.abort();
    await expect(done).rejects.toBeInstanceOf(UploadCancelled);
  });

  it("carries the API token itself when the server injected one, since fetch's wrapper can't see it", async () => {
    window.__kithToken = "secret";
    void uploadWorkspaceFile(file(), ".").catch(() => {});
    expect(FakeXHR.sent[0].headers["X-Kith-Token"]).toBe("secret");
  });
});

describe("keepBothName", () => {
  it("numbers before the extension, past every name taken", () => {
    expect(keepBothName("report.pdf", ["report.pdf"])).toBe("report (2).pdf");
    expect(keepBothName("report.pdf", ["report.pdf", "report (2).pdf", "report (3).pdf"])).toBe("report (4).pdf");
  });

  it("counts on from a number already there rather than stacking another", () => {
    expect(keepBothName("report (2).pdf", ["report (2).pdf"])).toBe("report (3).pdf");
  });

  it("treats a dotfile's dot as part of its name, and a bare name as having no extension", () => {
    expect(keepBothName(".env", [".env"])).toBe(".env (2)");
    expect(keepBothName("Makefile", ["Makefile"])).toBe("Makefile (2)");
    expect(keepBothName("archive.tar.gz", [])).toBe("archive.tar (2).gz");
  });
});

describe("searchWorkspace", () => {
  it("answers an older server's 404 with 'not supported' rather than an error", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 404 }));
    await expect(searchWorkspace("retry")).resolves.toEqual(SEARCH_UNSUPPORTED);
  });

  it("asks with the query and limit, and passes the signal through", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ results: [], truncated: true }), { status: 200 }));
    const stop = new AbortController();
    await expect(searchWorkspace("retry policy", { limit: 20, signal: stop.signal })).resolves.toEqual({
      results: [],
      truncated: true,
      supported: true,
    });
    const [url, init] = spy.mock.calls[0];
    expect(String(url)).toBe("/api/workspace/search?q=retry+policy&limit=20");
    expect(init?.signal).toBe(stop.signal);
  });

  it("throws what the server said for any other refusal", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ error: "bad folder" }), { status: 400 }));
    await expect(searchWorkspace("x")).rejects.toThrow("bad folder");
  });
});
