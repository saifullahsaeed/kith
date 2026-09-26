/**
 * The folder the conversation's project lives in, for anything inside a message that shows a path.
 *
 * A tool row names the file it touched, and every file in a project turn starts with the same
 * forty characters of `/Users/…/Desktop/<project>/`. Rows truncate at the end, so the prefix was
 * the part you saw and the file name was the part cut off. With the root known, the row says
 * `webapp/static/app.js`.
 *
 * A context, like `ConversationProvider`, for the same reason: the pane knows its project and a
 * message deep inside it has no other way to ask. `null` outside a project, which leaves paths
 * absolute with `~` for home — still right, just longer.
 */
import { createContext, useContext, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";

import { fetchProjects } from "@/lib/backend/brain";
import { keys } from "@/lib/query-keys";

const ProjectRootContext = createContext<string | null>(null);

export function ProjectRootProvider({
  projectId,
  children,
}: {
  projectId: number | null;
  children: ReactNode;
}) {
  // The same list the sidebar holds, so this is free once the app has loaded.
  const { data: projects = [] } = useQuery({
    queryKey: keys.projects(),
    queryFn: fetchProjects,
    enabled: projectId != null,
  });
  const root = projectId == null ? null : (projects.find((one) => one.id === projectId)?.directory ?? null);
  return <ProjectRootContext.Provider value={root || null}>{children}</ProjectRootContext.Provider>;
}

/** The project folder this subtree's paths are relative to, or `null`. */
export function useProjectRoot(): string | null {
  return useContext(ProjectRootContext);
}
