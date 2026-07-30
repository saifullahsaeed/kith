import { useEffect, useState } from "react";

import { FilePreviewDialog } from "@/components/file-view";
import { fetchWorkspaceFile } from "@/lib/backend/brain";
import { handOffAndOpen, useFileViewer } from "@/lib/files";

/**
 * The file viewer, mounted once and driven by the store.
 *
 * Mounted at the top level rather than beside each thing that can open a file: a path
 * in a chat message, a task deliverable and the file browser all open the same viewer,
 * and giving each its own copy would mean three of them could be open at once.
 */
export function WorkspaceFileViewer() {
  const path = useFileViewer((state) => state.path);
  const close = useFileViewer((state) => state.close);
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!path) return;
    setContent(null);
    setError("");
    let cancelled = false;
    // The same reader the file browser and task deliverables use. Writing a second one
    // here is how I got a decoder that assumed base64 and then failed on the first
    // em-dash in one of his reports.
    fetchWorkspaceFile(path)
      .then((file) => {
        if (!cancelled) setContent(file.content);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [path]);

  if (!path) return null;

  return (
    <FilePreviewDialog
      open
      onOpenChange={(next) => !next && close()}
      name={path}
      content={content}
      error={error}
      onOpenOnHost={(reveal) => handOffAndOpen(path, reveal)}
    />
  );
}
