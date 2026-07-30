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
  /** True when a whole folder came out — a page needs its stylesheet alongside it. */
  folderHandedOver: boolean;
  /** What this machine opens it with, when that could be determined. */
  opensWith: string | null;
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

// -- managing them ---------------------------------------------------------- //

async function change(url: string, body: unknown, method = "POST"): Promise<void> {
  const response = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))) as { error?: string };
    throw new Error(detail.error ?? `that didn't work (${response.status})`);
  }
}

export const makeFolder = (path: string) => change("/api/workspace/folder", { path });
export const rename = (path: string, to: string) => change("/api/workspace/rename", { path, to });
export const remove = (path: string) => change("/api/workspace/file", { path }, "DELETE");

/** Copy text to the clipboard, falling back for a non-secure origin.
 *
 * `navigator.clipboard` needs a secure context, and Kith is served over plain http on
 * loopback — which browsers do treat as secure, but Electron's behaviour has varied, so
 * the old path stays as a backstop rather than leaving "Copy path" silently dead. */
export async function copyText(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    return;
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
}

/** "2.4 KB", "1.1 MB" — sizes as a person reads them. */
export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

/** "14:23" today, "Mon 09:41" this week, "12 Jun" beyond — the shape a file browser
 *  uses, because the useful part of a date is however recent it is. */
export function formatModified(epochSeconds: number): string {
  if (!epochSeconds) return "";
  const when = new Date(epochSeconds * 1000);
  const now = new Date();
  const sameDay = when.toDateString() === now.toDateString();
  if (sameDay) {
    return when.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  }
  const days = (now.getTime() - when.getTime()) / 86_400_000;
  if (days < 7) {
    return when.toLocaleDateString(undefined, { weekday: "short" });
  }
  return when.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}
