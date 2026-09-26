/**
 * The server's model settings, for anything deep in the tree that changes them.
 *
 * Thinking, access and the model used to live in the title bar, which is the one place the config
 * was passed to. They moved into the composer — they are settings for the next message, so they
 * belong where the next message is written — and the composer is four components below anything
 * that held the config. A context rather than a prop threaded through the workspace, the pane and
 * the thread, none of which have any use for it.
 */
import { createContext, useContext, type ReactNode } from "react";

import type { ServerConfig } from "@/lib/backend/types";

interface ServerConfigValue {
  config: ServerConfig | null;
  onSaveConfig: (config: ServerConfig) => void;
}

const ServerConfigContext = createContext<ServerConfigValue>({
  config: null,
  onSaveConfig: () => {},
});

export function ServerConfigProvider({
  config,
  onSaveConfig,
  children,
}: ServerConfigValue & { children: ReactNode }) {
  return (
    <ServerConfigContext.Provider value={{ config, onSaveConfig }}>{children}</ServerConfigContext.Provider>
  );
}

export function useServerConfig(): ServerConfigValue {
  return useContext(ServerConfigContext);
}
