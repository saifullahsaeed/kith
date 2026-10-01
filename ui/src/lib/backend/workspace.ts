/**
 * Searching his folder, and putting files into it — the two workspace calls the Files screen and
 * the ⌘K palette added on top of the listing and reading ones in `brain.ts`.
 *
 * Their own module because neither is a JSON round trip like the rest: search is cancelled under
 * the person's typing, and upload needs progress, which `fetch` still cannot report for a request
 * body — so it is the one `XMLHttpRequest` in the app, and carries the API token itself.
 */
import type { WorkspaceEntry } from "./brain";

/* ── Search ─────────────────────────────────────────────────────────────── */

/** One thing `/api/workspace/search` found: a file or folder by its name, or a line in a file. */
export interface WorkspaceHit {
  /** Relative to his workspace root — what the file viewer opens. */
  path: string;
  name: string;
  kind: "name" | "content";
  type: "dir" | "file";
  /** The line it was found on, for a content match. One-based. */
  line?: number;
  /** That line, trimmed — and cut around the match when it is long. */
  text?: string;
  size: number;
  modified: number;
}

export interface WorkspaceSearch {
  results: WorkspaceHit[];
  /** It stopped at its count or its clock, so there may be more than these. */
  truncated: boolean;
  /** False when the server is older than the search route: names-only is then the whole story,
   *  and the palette should say so rather than claim it looked inside files. */
  supported: boolean;
}

/** What an older server answers with: nothing, and the fact that it could not look. */
export const SEARCH_UNSUPPORTED: WorkspaceSearch = { results: [], truncated: false, supported: false };

export async function searchWorkspace(
  query: string,
  { limit = 50, signal }: { limit?: number; signal?: AbortSignal } = {},
): Promise<WorkspaceSearch> {
  const q = new URLSearchParams({ q: query, limit: String(limit) });
  const res = await fetch(`/api/workspace/search?${q}`, { signal });
  // 404/405: a server from before this route. Not an error to show — the palette still has the
  // names it already knew, which is what it had before the route existed.
  if (res.status === 404 || res.status === 405) return SEARCH_UNSUPPORTED;
  const body = (await res.json().catch(() => null)) as
    | { results?: WorkspaceHit[]; truncated?: boolean; error?: string }
    | null;
  if (!res.ok) throw new Error(body?.error || "couldn't search his folder");
  if (!body || !Array.isArray(body.results)) return SEARCH_UNSUPPORTED;
  return { results: body.results, truncated: Boolean(body.truncated), supported: true };
}

/* ── Upload ─────────────────────────────────────────────────────────────── */

/** The server's ceiling for one file (`services/uploads.MAX_UPLOAD_BYTES`), mirrored so a file over it
 *  is refused before 200 MB of it is sent only to be refused at the end. */
export const MAX_UPLOAD_BYTES = 200 * 1024 * 1024;

/** What is in a folder after an upload — a listing entry, plus where it is. */
export interface UploadedFile extends WorkspaceEntry {
  path: string;
}

/** The name was taken. Carries what is there, so the question can say what replacing would lose. */
export class UploadConflict extends Error {
  // Declared and assigned rather than a parameter property: `erasableSyntaxOnly` rules those out.
  existing: UploadedFile;

  constructor(message: string, existing: UploadedFile) {
    super(message);
    this.name = "UploadConflict";
    this.existing = existing;
  }
}

/** The upload was stopped from here. Its own class so a cancel is not reported as a failure. */
export class UploadCancelled extends Error {
  constructor() {
    super("cancelled");
    this.name = "UploadCancelled";
  }
}

export interface UploadOptions {
  /** Replace what is there. Only ever the answer to an `UploadConflict`. */
  overwrite?: boolean;
  /** Save it under this name instead of the file's own — how "keep both" is done. */
  name?: string;
  /** Bytes sent so far, of how many. */
  onProgress?: (sent: number, total: number) => void;
  signal?: AbortSignal;
}

/**
 * Send one file into `folder` (relative to his root, "." for the root itself).
 *
 * `XMLHttpRequest` rather than `fetch` for `upload.onprogress` alone. That also means the global
 * `fetch` wrapper in `api-token.ts` does not see this request, so the token is attached here the
 * same way: from `window.__kithToken` when the server injected one, and not at all under the Vite
 * proxy, which adds it as it forwards.
 */
export function uploadWorkspaceFile(file: File, folder: string, options: UploadOptions = {}): Promise<UploadedFile> {
  return new Promise((resolve, reject) => {
    if (options.signal?.aborted) return reject(new UploadCancelled());
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/workspace/upload");
    const token = typeof window !== "undefined" ? window.__kithToken : undefined;
    if (token) xhr.setRequestHeader("X-Kith-Token", token);
    xhr.responseType = "text";

    xhr.upload.onprogress = (event) => {
      options.onProgress?.(event.loaded, event.lengthComputable ? event.total : file.size);
    };
    xhr.onload = () => {
      let body: { error?: string; existing?: UploadedFile } & Partial<UploadedFile> = {};
      try {
        body = JSON.parse(xhr.responseText || "{}");
      } catch {
        /* a body that is not JSON is answered by the status alone */
      }
      if (xhr.status >= 200 && xhr.status < 300) return resolve(body as UploadedFile);
      if (xhr.status === 409 && body.existing) {
        return reject(new UploadConflict(body.error || `${file.name} is already there`, body.existing));
      }
      if (xhr.status === 404) return reject(new Error("This server can't take uploads yet — restart Kith to update it."));
      reject(new Error(body.error || `couldn't upload ${file.name} (${xhr.status})`));
    };
    xhr.onerror = () => reject(new Error(`couldn't reach the server to upload ${file.name}`));
    xhr.onabort = () => reject(new UploadCancelled());
    options.signal?.addEventListener("abort", () => xhr.abort(), { once: true });

    const form = new FormData();
    form.append("folder", folder || ".");
    if (options.overwrite) form.append("overwrite", "1");
    form.append("file", file, options.name ?? file.name);
    xhr.send(form);
  });
}

/**
 * The name "keep both" saves under: `report (2).pdf`, then `(3)`, past every name in `taken`.
 *
 * Counted from an existing "(n)" rather than stacked on it, so keeping both of `report (2).pdf`
 * makes `report (3).pdf` and not `report (2) (2).pdf`. A dotfile's dot is part of its name, not
 * an extension: `.env` becomes `.env (2)`.
 */
export function keepBothName(name: string, taken: Iterable<string>): string {
  const names = new Set(taken);
  const dot = name.lastIndexOf(".");
  const [stem, ext] = dot > 0 ? [name.slice(0, dot), name.slice(dot)] : [name, ""];
  const numbered = /^(.*) \((\d+)\)$/.exec(stem);
  const base = numbered ? numbered[1] : stem;
  for (let n = numbered ? Number(numbered[2]) + 1 : 2; n < 10_000; n++) {
    const candidate = `${base} (${n})${ext}`;
    if (!names.has(candidate)) return candidate;
  }
  return `${base} (${Date.now()})${ext}`;
}
