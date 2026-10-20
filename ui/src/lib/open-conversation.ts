/**
 * How something deep inside a pane opens another conversation.
 *
 * Agents made this a common thing to want. A report in the conductor's thread names the agent that
 * wrote it, an agent's page names the chain it works for, and the card a `start_agents` call leaves
 * lists who was started — every one of those names is a way into another conversation, and none of
 * them sits anywhere near the workspace that knows how to open one.
 *
 * Not a route. `/chat/<id>` opens a conversation only when the address *changes*, so following a
 * link to a chat whose tab you have since closed, while the address still names it, did nothing.
 * The workspace's own `openConversation` focuses the tab or opens it, and remembers it for the next
 * reload; the workspace provides it as `<OpenConversation.Provider value={openConversation}>`.
 * Outside a workspace — a test, a pane rendered alone — there is nothing to open into, and the
 * default does nothing rather than pretending.
 *
 * The context itself is exported rather than wrapped in a provider component, so this module has no
 * component in it and stays a plain `.ts` file that fast refresh has no opinion about.
 */
import { createContext, useContext } from "react";

export const OpenConversation = createContext<(id: string) => void>(() => {});

/** Open (or focus) a conversation by id. */
export function useOpenConversation(): (id: string) => void {
  return useContext(OpenConversation);
}
