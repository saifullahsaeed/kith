import { useEffect, useState } from "react";

import { FileViewer, skipTextRead } from "@/components/files";
import { AmbiguousPath, fetchWorkspaceFile } from "@/lib/backend/brain";
import type { Candidate } from "@/lib/backend/brain";
import { openWorkspaceFile, useFileProject, useFileViewer } from "@/lib/files";

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
  /* The same project id, published for the one other thing that needs it.
   *
   * `useOpenable` asks the server whether a path in his prose leads anywhere, and that answer
   * depends on the project exactly as reading the file does. Set here rather than threaded
   * from the workspace down through every message and paragraph to reach a code span — this
   * component already receives it, already sits at the top level, and is already mounted
   * unconditionally, so it is the one place that can say it once. */
  const bind = useFileProject((state) => state.bind);
  useEffect(() => {
    bind(projectId ?? null);
  }, [bind, projectId]);
  const close = useFileViewer((state) => state.close);
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState("");
  /** The files a name turned out to match, when it matched several. Held rather than parsed
   *  back out of the message, so picking one is a click on a path the server named. */
  const [candidates, setCandidates] = useState<Candidate[]>([]);
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
    setCandidates([]);
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
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        if (err instanceof AmbiguousPath) setCandidates(err.candidates);
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
      candidates={candidates}
      // Picking one re-enters through the same store, so it takes the ordinary read path:
      // an absolute path, which `locate` returns as given rather than searching for again.
      onPick={(picked) => useFileViewer.getState().open(picked)}
      projectId={projectId}
      onOpenOnHost={(reveal) => openWorkspaceFile(path, reveal, projectId)}
    />
  );
}
