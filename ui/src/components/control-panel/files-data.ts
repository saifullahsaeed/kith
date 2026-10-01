/** The Files screen's reads, shared by the list, the tree and the preview. */
import { useQuery } from "@tanstack/react-query";

import { fetchWorkspace, fetchWorkspaceFile } from "@/lib/backend/brain";
import type { WorkspaceEntry } from "@/lib/backend/brain";
import { keys } from "@/lib/query-keys";

/** One folder's listing — the same key everywhere, so the tree and the list share one answer. */
export function useFolder(dir: string, enabled: boolean) {
  return useQuery({
    queryKey: keys.workspace(dir),
    queryFn: async (): Promise<WorkspaceEntry[]> => (await fetchWorkspace(dir)).entries,
    enabled,
  });
}

/** One file's text, for the preview. No retry: "not text" and "too big" are answers, not faults. */
export function useFileText(path: string) {
  return useQuery({
    queryKey: keys.workspaceFile(path),
    queryFn: async () => (await fetchWorkspaceFile(path)).content,
    retry: false,
  });
}
