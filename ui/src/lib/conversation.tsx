/**
 * Which conversation the thing you are rendering belongs to.
 *
 * A surface drawn inside a reply — a canvas, an animated diagram — reports things back: what a
 * control was set to, a choreography it could not run. Those reports ride along with the next
 * message, and until now they rode along with *whichever chat sent one next*, because the stores
 * holding them are global and the app opens several chats at once.
 *
 * The pane has always known the answer; there was simply no way to ask it from inside a message.
 * A context rather than a module-level slot, and that is the whole point: a slot has one value for
 * the process, which is exactly the bug. A context has one value per subtree, and a subtree here is
 * a conversation.
 *
 * `""` outside any pane, and outside any pane there is no conversation — so the readers treat it as
 * "nothing is mine", which is both true and the safe direction.
 */
import { createContext, useContext, type ReactNode } from "react";

const ConversationContext = createContext("");

export function ConversationProvider({
  id,
  children,
}: {
  id: string;
  children: ReactNode;
}) {
  return (
    <ConversationContext.Provider value={id}>
      {children}
    </ConversationContext.Provider>
  );
}

/** The conversation this subtree is part of, or `""` when it is not part of one. */
export function useConversationId(): string {
  return useContext(ConversationContext);
}
