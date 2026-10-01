/**
 * The upload queue behind the Files screen's Upload button and drop target — see `files-upload.tsx`
 * for the tray and the target, and for why it sends one file at a time.
 *
 * A hook and its helpers, in a file of their own so the component file exports only components
 * (Fast Refresh reloads a file that mixes the two from scratch).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import type { WorkspaceEntry } from "@/lib/backend/brain";
import {
  MAX_UPLOAD_BYTES,
  UploadCancelled,
  UploadConflict,
  keepBothName,
  uploadWorkspaceFile,
  type UploadedFile,
} from "@/lib/backend/workspace";
import { formatSize } from "@/lib/files";
import { keys } from "@/lib/query-keys";

/* ── The queue ──────────────────────────────────────────────────────────── */

export interface UploadItem {
  id: string;
  file: File;
  /** Where it is going, relative to his root ("." for the root itself). */
  folder: string;
  /** What it will be called there — the file's own name, until "keep both" picks another. */
  name: string;
  state: "queued" | "sending" | "done" | "conflict" | "failed";
  sent: number;
  total: number;
  overwrite?: boolean;
  /** Set by "keep both": a second 409 then moves on to the next free name rather than asking again. */
  keepBoth?: boolean;
  /** Names known to be taken in the folder, so each retry of "keep both" goes past all of them. */
  taken?: string[];
  existing?: UploadedFile;
  error?: string;
}

/** How long a finished row stays in the tray: long enough to see it land. */
const DONE_LINGER_MS = 2500;
/** "Keep both" gives up after this many taken names in a row — a folder racing it is not a folder
 *  any number of retries will win against. */
const KEEP_BOTH_TRIES = 25;

let nextId = 0;

/** The folder name the tray and the drop target print: "src", or "his folder" for the root. */
export function folderName(folder: string): string {
  return folder === "." || folder === "" ? "his folder" : (folder.split("/").pop() ?? folder);
}

export function useUploads(onLanded: (folder: string, saved: UploadedFile) => void) {
  const cache = useQueryClient();
  const [items, setItems] = useState<UploadItem[]>([]);
  const running = useRef(false);
  const controllers = useRef(new Map<string, AbortController>());
  // A tick so the pump re-runs once a send finishes, whether or not that send changed the list.
  const [tick, setTick] = useState(0);
  const landed = useRef(onLanded);
  useEffect(() => {
    landed.current = onLanded;
  }, [onLanded]);

  const patch = useCallback((id: string, change: Partial<UploadItem>) => {
    setItems((list) => list.map((item) => (item.id === id ? { ...item, ...change } : item)));
  }, []);
  const drop = useCallback((id: string) => {
    setItems((list) => list.filter((item) => item.id !== id));
  }, []);

  /** The names a folder listing already shows — what "keep both" must not land on. */
  const namesIn = useCallback(
    (folder: string): string[] => (cache.getQueryData<WorkspaceEntry[]>(keys.workspace(folder)) ?? []).map((e) => e.name),
    [cache],
  );

  const send = useCallback(
    async (item: UploadItem) => {
      const controller = new AbortController();
      controllers.current.set(item.id, controller);
      patch(item.id, { state: "sending", sent: 0, total: item.file.size, error: undefined });
      let shown = -1;
      try {
        const saved = await uploadWorkspaceFile(item.file, item.folder, {
          overwrite: item.overwrite,
          name: item.name,
          signal: controller.signal,
          // A percent at a time: progress fires per chunk, and a row re-rendered for every 64 KB of
          // a large file is a tray that makes the list beside it stutter.
          onProgress: (sent, total) => {
            const percent = total ? Math.floor((sent / total) * 100) : 0;
            if (percent === shown) return;
            shown = percent;
            patch(item.id, { sent, total });
          },
        });
        patch(item.id, { state: "done", sent: item.file.size });
        void cache.invalidateQueries({ queryKey: keys.workspace(item.folder) });
        landed.current(item.folder, saved);
        window.setTimeout(() => drop(item.id), DONE_LINGER_MS);
      } catch (error) {
        if (error instanceof UploadCancelled) return drop(item.id);
        if (error instanceof UploadConflict) {
          const taken = [...(item.taken ?? []), item.name, error.existing.name];
          if (item.keepBoth && taken.length < KEEP_BOTH_TRIES) {
            return patch(item.id, { state: "queued", taken, name: keepBothName(item.name, [...taken, ...namesIn(item.folder)]) });
          }
          return patch(item.id, { state: "conflict", existing: error.existing, taken });
        }
        patch(item.id, { state: "failed", error: error instanceof Error ? error.message : "that didn't upload" });
      } finally {
        controllers.current.delete(item.id);
      }
    },
    [cache, drop, namesIn, patch],
  );

  // The pump: whenever nothing is sending, send the first thing waiting.
  useEffect(() => {
    if (running.current) return;
    const next = items.find((item) => item.state === "queued");
    if (!next) return;
    running.current = true;
    void send(next).finally(() => {
      running.current = false;
      setTick((n) => n + 1);
    });
  }, [items, tick, send]);

  // Leaving the screen stops what is still going, rather than finishing it out of sight.
  useEffect(() => {
    const live = controllers.current;
    return () => {
      for (const controller of live.values()) controller.abort();
    };
  }, []);

  /** Queue these files for `folder`. Folders dropped from Finder and files over the ceiling are
   *  refused on their row straight away, rather than sent to find out. */
  const add = useCallback((files: Iterable<File>, folder: string, folders: Set<string> = new Set()) => {
    const fresh: UploadItem[] = [];
    for (const file of files) {
      const item: UploadItem = {
        id: `upload-${++nextId}`,
        file,
        folder,
        name: file.name,
        state: "queued",
        sent: 0,
        total: file.size,
      };
      if (folders.has(file.name)) {
        item.state = "failed";
        item.error = "Folders can't be uploaded yet — open it and drop the files inside.";
      } else if (file.size > MAX_UPLOAD_BYTES) {
        item.state = "failed";
        item.error = `Over the ${formatSize(MAX_UPLOAD_BYTES)} limit for one file.`;
      }
      fresh.push(item);
    }
    if (fresh.length) setItems((list) => [...list, ...fresh]);
  }, []);

  const replace = useCallback((id: string) => patch(id, { state: "queued", overwrite: true }), [patch]);
  const keepBoth = useCallback(
    (id: string) =>
      setItems((list) =>
        list.map((item) => {
          if (item.id !== id) return item;
          const taken = [...(item.taken ?? []), item.name, ...namesIn(item.folder)];
          return { ...item, state: "queued", keepBoth: true, taken, name: keepBothName(item.name, taken) };
        }),
      ),
    [namesIn],
  );
  const cancel = useCallback(
    (id: string) => {
      const controller = controllers.current.get(id);
      if (controller) controller.abort();
      else drop(id);
    },
    [drop],
  );

  return { items, add, replace, keepBoth, cancel, dismiss: drop };
}

/* ── What a drop carries ────────────────────────────────────────────────── */

/** Whether a drag is files from the machine — not text, not a row being dragged inside the page. */
export function carriesFiles(event: React.DragEvent): boolean {
  return Array.from(event.dataTransfer?.types ?? []).includes("Files");
}

/** The files a drop delivered, and the names among them that are folders.
 *
 *  A folder dropped from Finder arrives as a `File` too — empty and typeless, indistinguishable by
 *  its fields — so the entry API is what tells them apart. Where it is missing, everything is
 *  treated as a file and a folder fails on its row, which is still an answer. */
export function dropped(event: React.DragEvent): { files: File[]; folders: Set<string> } {
  const transfer = event.dataTransfer;
  const folders = new Set<string>();
  for (const item of Array.from(transfer.items ?? [])) {
    const entry = typeof item.webkitGetAsEntry === "function" ? item.webkitGetAsEntry() : null;
    if (entry?.isDirectory) folders.add(entry.name);
  }
  return { files: Array.from(transfer.files ?? []), folders };
}
