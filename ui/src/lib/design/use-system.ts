/**
 * Which design system this workspace is drawn in, found rather than configured.
 *
 * Same bet as everything else on this surface: a system that has to be selected in a settings
 * screen is a system nobody selects. The files are looked for in order and the first that parses
 * wins — a `design-system.json` he wrote for screens beats a brand profile written for slides,
 * because the one authored for this purpose should outrank the one adapted into it.
 *
 * Kith's own nine are the answer when there is nothing, so a workspace with no system still has
 * a coherent palette and nothing has to handle "no system" as a special case.
 */
import { useQueries } from "@tanstack/react-query";

import { fetchWorkspaceFile } from "@/lib/backend/brain";
import { kithSystem, readSystem, SYSTEM_FILES, type System } from "@/lib/design/system";
import { paletteFor } from "@/lib/kith-palette";
import { useDarkMode } from "@/lib/theme";

export function useDesignSystem(projectId: number | null): System {
  const dark = useDarkMode();
  const files = useQueries({
    queries: SYSTEM_FILES.map((path) => ({
      queryKey: ["design-system", path, projectId],
      // A workspace with no system is the ordinary case, not an error.
      queryFn: () => fetchWorkspaceFile(path, projectId).catch(() => ({ path, content: "" })),
      staleTime: 60_000,
    })),
  });

  for (const file of files) {
    const content = file.data?.content;
    if (!content) continue;
    const system = readSystem(content);
    if (system) return system;
  }
  return kithSystem(paletteFor(dark));
}
