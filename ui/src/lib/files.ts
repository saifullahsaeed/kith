/** Recognising his files in what he writes, and getting them onto your machine.
 *
 * His files live in a Docker container, so a path he mentions is not something the
 * rest of the machine can see. These two things fix that: spot the mention, and hand
 * the file out.
 */

import { create } from "zustand";

/** Suffixes worth treating as a file when he writes one in backticks. Not "anything
 *  with a dot" — that matches version numbers, domain names and `object.method`. */
const KNOWN_SUFFIXES = [
  "md",
  "txt",
  "csv",
  "tsv",
  "json",
  "yaml",
  "yml",
  "toml",
  "ini",
  "cfg",
  "conf",
  "env",
  "py",
  "js",
  "mjs",
  "ts",
  "tsx",
  "jsx",
  "sh",
  "bash",
  "zsh",
  "rb",
  "go",
  "rs",
  "java",
  "c",
  "h",
  "cpp",
  "sql",
  "html",
  "htm",
  "css",
  "scss",
  "xml",
  "svg",
  "pdf",
  "xlsx",
  "xls",
  "docx",
  "doc",
  "pptx",
  "png",
  "jpg",
  "jpeg",
  "gif",
  "webp",
  "zip",
  "tar",
  "gz",
  "log",
  "ipynb",
];

const FILE_PATTERN = new RegExp(
  // An optional /home/kith prefix or a relative path, then at least one name, then a
  // known suffix. Anchored at both ends: a path is the whole span, not part of a
  // sentence that happens to contain one.
  `^(?:/home/kith/|~/|\\./)?(?:[\\w.-]+/)*[\\w.-]+\\.(${KNOWN_SUFFIXES.join("|")})$`,
  "i",
);

/**
 * Does this inline-code span name one of his files?
 *
 * Only inline code is considered, which is the whole reason this is safe. Scanning
 * prose for path-shaped text mangles ordinary writing ("and/or", "TypeScript 5.4"),
 * and scanning fenced blocks would linkify source code. He writes paths in backticks
 * already, so the signal is there without guessing.
 */
export function looksLikeHisFile(text: string): boolean {
  const trimmed = text.trim();
  if (!trimmed || trimmed.length > 200 || /\s/.test(trimmed)) return false;
  // A URL is a link already and means something else entirely.
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(trimmed)) return false;
  return FILE_PATTERN.test(trimmed);
}

/** His home inside the sandbox — stripped so the server's own path anchoring applies. */
export function toSandboxPath(text: string): string {
  return text
    .trim()
    .replace(/^\/home\/kith\//, "")
    .replace(/^~\//, "")
    .replace(/^\.\//, "");
}

// -- opening one ------------------------------------------------------------ //

export interface HandoffResult {
  sandboxPath: string;
  hostPath: string;
  folder: string;
  size: number;
  /** False for anything that would execute; revealing it is still offered. */
  openable: boolean;
  note: string;
}

/** Copy a file out of the sandbox onto this machine. */
export async function handOff(path: string): Promise<HandoffResult> {
  const response = await fetch("/api/workspace/handoff", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  const body = (await response.json()) as HandoffResult & { error?: string };
  if (!response.ok) throw new Error(body.error ?? `couldn't export (${response.status})`);
  return body;
}

/** Open it with whatever this machine uses for that type, or show it in the folder. */
export async function openOnHost(hostPath: string, reveal = false): Promise<void> {
  const response = await fetch("/api/workspace/open", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ hostPath, reveal }),
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { error?: string };
    throw new Error(body.error ?? `couldn't open it (${response.status})`);
  }
}

/** Export then open in one go, which is what clicking "Open in…" means. */
export async function handOffAndOpen(path: string, reveal = false): Promise<HandoffResult> {
  const result = await handOff(path);
  // A file that would execute is revealed instead — the server refuses to run it, and
  // arriving at an error dialog would be a worse answer than showing it in the folder.
  await openOnHost(result.hostPath, reveal || !result.openable);
  return result;
}

// -- the viewer, openable from anywhere ------------------------------------- //

/**
 * Which of his files is being looked at.
 *
 * A store rather than props because the request can come from anywhere — a path in a
 * chat message, a task deliverable, the file browser — and threading a callback from
 * the workspace down through the markdown renderer would mean every component in
 * between knowing about file viewing.
 */
export const useFileViewer = create<{
  path: string | null;
  open: (path: string) => void;
  close: () => void;
}>((set) => ({
  path: null,
  open: (path) => set({ path: toSandboxPath(path) }),
  close: () => set({ path: null }),
}));
