/** Client for the plugin API — what is installed, and what tabs it contributes.
 *
 * Note what is *not* here, and it is the same omission `mcp.ts` documents: a way to read an
 * environment value back. The server sends key names and never values, because that is where a
 * plugin's API token goes and a settings page that round-trips one hands it to anything that can
 * read the response. Editing a value means typing it again.
 */

import type { PluginSurface } from "@/lib/plugin-index";

export interface PluginCommand {
  name: string;
  title: string;
  description: string;
  delivery: "host" | "state" | "surface";
  surface: string;
  present: { in?: string; icon?: string; when?: string };
  /** Whether it costs prompt tokens by being offered to him. Defaults false in a manifest. */
  model: boolean;
}

export interface Plugin {
  id: string;
  name: string;
  version: string;
  description: string;
  publisher: string;
  homepage: string;
  license: string;
  path: string;
  server: {
    command: string;
    args: string[];
    /** Names only. Never their values. */
    envKeys: string[];
    reach: { read: string[]; write: string[]; network: boolean };
  } | null;
  surfaces: Omit<PluginSurface, "plugin" | "pluginName">[];
  commands: PluginCommand[];
  skills: string[];
  state: Record<string, unknown>;
  /** Manifest fields this build has no concept of — shown rather than silently dropped. */
  unsupportedFields: string[];
  /** What it adds to every request, forever, if fully enabled. */
  promptChars: number;
  promptTokens: number;
  problems: string[];
  /** What the *person* decided, kept separate from what the manifest offers. A screen that
   *  merged them could render a default as though somebody had chosen it. */
  decided: {
    enabled: boolean;
    digest: boolean;
    envKeys: string[];
    installedAt: string;
    version: string;
  };
}

export interface PluginTrouble {
  id: string;
  kind: "broken" | "incompatible" | "orphan" | "stray";
  error: string;
}

export interface PluginStateRow {
  plugin: string;
  keys: number;
  bytes: number;
  limit: number;
  digestOn: boolean;
  /** The digest line he is actually given, verbatim. The one thing that answers "why does he
   *  not know about my state" in a glance. */
  digestLine: string;
}

export interface PluginsSnapshot {
  root: string;
  plugins: Plugin[];
  problems: PluginTrouble[];
  state: PluginStateRow[];
  promptChars: number;
  promptTokens: number;
  promptLimit: number;
}

export interface PluginReview {
  plugin: Plugin;
  faults: string[];
  installable: boolean;
  replacing: Record<string, unknown> | null;
  signature: string;
  promptChars: number;
  promptTokens: number;
  installedPromptChars: number;
}

export async function fetchPlugins(): Promise<PluginsSnapshot> {
  const response = await fetch("/api/plugins");
  if (!response.ok) throw new Error(`/api/plugins returned ${response.status}`);
  return (await response.json()) as PluginsSnapshot;
}

export async function fetchPluginSurfaces(): Promise<PluginSurface[]> {
  const response = await fetch("/api/plugins/surfaces");
  if (!response.ok) throw new Error(`/api/plugins/surfaces returned ${response.status}`);
  return ((await response.json()).surfaces ?? []) as PluginSurface[];
}

/** Read a folder as a plugin without installing it. Writes nothing — see `install`. */
export async function reviewPlugin(path: string): Promise<PluginReview> {
  const response = await fetch(`/api/plugins/review?path=${encodeURIComponent(path)}`);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `could not read that folder (${response.status})`);
  return body as PluginReview;
}

export async function installPlugin(
  path: string,
  env: Record<string, string> = {},
): Promise<PluginsSnapshot> {
  const response = await fetch("/api/plugins", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, env }),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `could not install (${response.status})`);
  return body as PluginsSnapshot;
}

export async function patchPlugin(
  id: string,
  changes: { enabled?: boolean; digest?: boolean; env?: Record<string, string> },
): Promise<PluginsSnapshot> {
  const response = await fetch(`/api/plugins/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `could not save (${response.status})`);
  return body as PluginsSnapshot;
}

/** Remove a plugin. Its stored state survives thirty days unless `deleteData` says otherwise. */
export async function removePlugin(id: string, deleteData = false): Promise<PluginsSnapshot> {
  const response = await fetch(`/api/plugins/${id}${deleteData ? "?data=delete" : ""}`, {
    method: "DELETE",
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `could not remove (${response.status})`);
  return body as PluginsSnapshot;
}

export interface PluginStateRead {
  values: Record<string, unknown>;
  revisions: Record<string, number>;
  slot: { keys: number; bytes: number; limit: number };
}

/** What a plugin is holding, for its surface to render from.
 *
 * Scoped to a conversation because that is how most plugins key their state; the server
 * verifies the scope against the plugin's own declaration rather than trusting this. */
export async function fetchPluginState(
  plugin: string,
  conversation: string,
): Promise<PluginStateRead> {
  const response = await fetch(
    `/api/plugins/${plugin}/state?conversation=${encodeURIComponent(conversation)}`,
  );
  if (!response.ok) throw new Error(`could not read ${plugin}'s state (${response.status})`);
  return (await response.json()) as PluginStateRead;
}

export interface PluginCall {
  id: string;
  conversation: string;
  plugin: string;
  command: string;
  view: string;
  instance: string;
  args: Record<string, unknown>;
  timeoutMs: number;
  repeatable: boolean;
}

/** What a surface is being asked, right now.
 *
 * Fetched on mount **and** on a `plugin_call` change — snapshot-then-subscribe, the same join
 * `use-activity` uses. That is what makes a call survive a renderer reload rather than being
 * lost along with the push that had already happened.
 *
 * `client` claims what it hands out, so two windows on one backend do not both deliver the same
 * call and race to answer it. */
export async function fetchPluginCalls(
  conversation: string,
  client: string,
  /** `surface` for a frame's own calls, `host` for effects the app performs. Both collectors
   *  poll this route and the server claims what it hands out, so a collector that asked for
   *  everything would take calls it cannot answer — see `Call.kind`. */
  kind: "surface" | "host" = "surface",
): Promise<PluginCall[]> {
  const query = new URLSearchParams({ conversation, client, kind });
  const response = await fetch(`/api/plugins/calls?${query}`);
  if (!response.ok) throw new Error(`could not read pending calls (${response.status})`);
  return ((await response.json()).calls ?? []) as PluginCall[];
}

export async function replyToPluginCall(
  id: string,
  value: Record<string, unknown>,
  ok = true,
): Promise<void> {
  await fetch(`/api/plugins/calls/${id}/reply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value, ok }),
  }).catch(() => {});
}

/** Bytes a surface handed back, written into the plugin's own storage.
 *
 * Posted raw with the type as the Content-Type, rather than as JSON with base64 in it: base64
 * is a third larger, and the reply is the path the model reads the file by. */
export async function putSurfaceFile(
  ticket: string,
  name: string,
  mime: string,
  bytes: ArrayBuffer,
): Promise<{ path: string } | null> {
  const response = await fetch(
    `/api/plugins/frame/${ticket}/file?name=${encodeURIComponent(name)}`,
    { method: "POST", headers: { "Content-Type": mime }, body: bytes },
  );
  if (!response.ok) return null;
  return (await response.json()) as { path: string };
}

/** A file the plugin holds, for its own surface to display.
 *
 * The frame cannot fetch — `default-src 'none'` — so the renderer reads it and hands the bytes
 * over the bridge, where the frame turns them into a `blob:` URL. */
export async function fetchPluginFile(plugin: string, path: string): Promise<ArrayBuffer | null> {
  const response = await fetch(
    `/api/plugins/${plugin}/file?path=${encodeURIComponent(path)}`,
  );
  if (!response.ok) return null;
  return await response.arrayBuffer();
}

/** Set off one of a plugin's commands as the person.
 *
 * `origin: "person"` on the server, which skips the permission gate — safe because the only
 * routes here are chrome Kith drew and a surface the manifest explicitly opted in per command. */
export async function runPluginCommand(
  plugin: string,
  command: string,
  args: Record<string, unknown>,
  conversation: string,
): Promise<void> {
  await fetch(`/api/plugins/${plugin}/command/${command}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ args, conversation }),
  }).catch(() => {});
}
