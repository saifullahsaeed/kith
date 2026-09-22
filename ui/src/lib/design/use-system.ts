/**
 * Which design system this screen is drawn in, asked once of the one thing that can see
 * everywhere.
 *
 * The interface used to look for the file itself, which found a `design-system.json` in the
 * workspace and missed the system that actually existed: the owner's brand lives in
 * `brand/profile.json` *inside a skill*, and the workspace routes cannot see in there. The
 * workaround was to copy it out, which is how two files come to disagree by next month. The
 * server resolves it now — project folder, then workspace, then installed skills — so the file
 * stays where it was authored and nothing is a copy.
 *
 * Kith's own nine are the answer when there is nothing, so a workspace with no system still has a
 * coherent palette and nothing downstream has to treat "no system" as a special case.
 */
import { useQuery } from "@tanstack/react-query";

import { fetchDesignSystem } from "@/lib/backend/brain";
import { kithSystem, readSystem, type System } from "@/lib/design/system";
import { paletteFor } from "@/lib/kith-palette";
import { useDarkMode } from "@/lib/theme";

export function useDesignSystem(projectId: number | null): System {
  const dark = useDarkMode();
  const found = useQuery({
    queryKey: ["design-system", projectId],
    queryFn: () => fetchDesignSystem(projectId),
    // A brand does not change between keystrokes, and every frame on a board asks.
    staleTime: 60_000,
  });

  const content = found.data?.content;
  const conventions = found.data?.conventions ?? "";
  return (content ? readSystem(content, conventions) : null) ?? kithSystem(paletteFor(dark));
}
