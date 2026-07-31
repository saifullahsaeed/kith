import { useEffect, useState } from "react";

import { FileViewer, skipTextRead } from "@/components/file-view";
import { fetchWorkspaceFile } from "@/lib/backend/brain";
import { openWorkspaceFile, useFileViewer } from "@/lib/files";

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
    // A picture or a PDF is loaded by the viewer from its own bytes, so reading it as
    // text here would spend a megabyte to produce a decode error — which is what the
    // viewer used to show, as "this one needs its own application".
    if (skipTextRead(path)) return;
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
    <FileViewer
      open
      onOpenChange={(next) => !next && close()}
      name={path}
      content={content}
      error={error}
      onOpenOnHost={(reveal) => openWorkspaceFile(path, reveal)}
    />
  );
}
