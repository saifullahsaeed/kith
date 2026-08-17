import { useEffect, useState } from "react";

import { FileViewer, skipTextRead } from "@/components/files";
import { fetchWorkspaceFile } from "@/lib/backend/brain";
import { openWorkspaceFile, useFileViewer } from "@/lib/files";

/**
 * The file viewer, mounted once and driven by the store.
 *
 * Mounted at the top level rather than beside each thing that can open a file: a path
 * in a chat message, a task deliverable and the file browser all open the same viewer,
 * and giving each its own copy would mean three of them could be open at once.
 *
 * `projectId` is this session's project, not the file's — because the store carries a bare
 * path and nothing else. The things that put one there are a path in his prose and a path in
 * a tool result, both written during a turn, and a relative path written during a turn is
 * relative to whatever project that turn was bound to. Reading it against the global root
 * instead is what produced "there's no .kith/work/task-76.md" for a file sitting exactly
 * where he left it. Null for a session working on nothing, which is the old behaviour.
 */
export function WorkspaceFileViewer({ projectId }: { projectId?: number | null }) {
  const path = useFileViewer((state) => state.path);
  const close = useFileViewer((state) => state.close);
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState("");
  /** Where the file turned out to be, when that isn't where it was named.
   *
   *  A click carries whatever he wrote, and what he writes is often a bare filename in a
   *  sentence — `Staff-SAIF.xlsx` for a file in `inbox/`. The server looks for it (see
   *  `locate`), so the title should say what was opened rather than what was clicked;
   *  otherwise the one useful fact, that it lives somewhere else, is the one thing missing. */
  const [found, setFound] = useState("");

  useEffect(() => {
    if (!path) return;
    setContent(null);
    setError("");
    setFound("");
    // A picture or a PDF is loaded by the viewer from its own bytes, so reading it as
    // text here would spend a megabyte to produce a decode error — which is what the
    // viewer used to show, as "this one needs its own application".
    if (skipTextRead(path)) return;
    let cancelled = false;
    // The same reader the file browser and task deliverables use. Writing a second one
    // here is how I got a decoder that assumed base64 and then failed on the first
    // em-dash in one of his reports.
    fetchWorkspaceFile(path, projectId)
      .then((file) => {
        if (cancelled) return;
        setContent(file.content);
        if (file.path) setFound(file.path);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [path, projectId]);

  if (!path) return null;

  return (
    <FileViewer
      open
      onOpenChange={(next) => !next && close()}
      name={found || path}
      content={content}
      error={error}
      projectId={projectId}
      onOpenOnHost={(reveal) => openWorkspaceFile(path, reveal, projectId)}
    />
  );
}
