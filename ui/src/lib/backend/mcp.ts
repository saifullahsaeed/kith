/** Client for MCP servers — other programs whose tools become his.
 *
 * Note what is *not* here: a way to read an environment value back. The server sends key
 * names and never values, because that is where an API token goes and a settings page that
 * round-trips one hands it to anything that can read the response. Editing a value means
 * typing it again, which is the correct trade.
 */

export interface MCPTool {
  name: string;
  description: string;
}

export interface MCPServer {
  label: string;
  command: string;
  args: string[];
  /** The names of the environment variables set for it. Never their values. */
  envKeys: string[];
  enabled: boolean;
  /** What is structurally wrong with it, before anything is run. */
  problems: string[];
  /** Whether it is up right now — a different question from `enabled`. */
  connected: boolean;
  /** What it is contributing, for the ones that are connected. */
  tools: MCPTool[];
}

/** What you send back: the same shape plus the env values you are setting. */
export interface MCPServerInput {
  label: string;
  command: string;
  args: string[];
  env: Record<string, string>;
  enabled: boolean;
}

export interface MCPProbe {
  ok: boolean;
  /** The server's own name and version, or why it could not be reached. */
  detail: string;
  tools: MCPTool[];
}

export async function fetchMCPServers(): Promise<MCPServer[]> {
  const response = await fetch("/api/mcp");
  if (!response.ok) throw new Error(`/api/mcp returned ${response.status}`);
  return (await response.json()).servers as MCPServer[];
}

/** Start it, ask what it offers, stop it. Saves nothing. */
export async function probeMCPServer(server: MCPServerInput): Promise<MCPProbe> {
  const response = await fetch("/api/mcp/probe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(server),
  });
  if (!response.ok) throw new Error(`could not try that server (${response.status})`);
  return (await response.json()) as MCPProbe;
}

/** Replace the whole list. Returns which servers failed to start, by label. */
export async function saveMCPServers(
  servers: MCPServerInput[],
): Promise<{ failed: Record<string, string> }> {
  const response = await fetch("/api/mcp", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ servers }),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `could not save (${response.status})`);
  return { failed: body.failed || {} };
}

export async function reconnectMCPServers(): Promise<{ failed: Record<string, string> }> {
  const response = await fetch("/api/mcp/reconnect", { method: "POST" });
  if (!response.ok) throw new Error(`could not reconnect (${response.status})`);
  const body = await response.json();
  return { failed: body.failed || {} };
}
