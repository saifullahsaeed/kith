import { useCallback, useEffect, useState } from "react";
import { Check, CircleDashed, Loader2, Plus, Plug, TriangleAlert, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/confirm";
import {
  fetchMCPServers,
  probeMCPServer,
  reconnectMCPServers,
  saveMCPServers,
  type MCPProbe,
  type MCPServer,
  type MCPServerInput,
} from "@/lib/backend/mcp";
import { cn } from "@/lib/utils";

/**
 * Other programs whose tools become his.
 *
 * An MCP server is a command, not a URL — it runs on this machine and talks over a pipe. So
 * the form is a command line, which is also how every server's own README describes itself
 * (`npx -y @modelcontextprotocol/server-filesystem /path`), and pasting one in should just
 * work rather than needing to be taken apart into fields.
 *
 * **Try before save is the whole shape of this.** Adding a server that does not start is the
 * common case — a typo, a package that needs installing, a missing token — and finding out
 * by saving it and watching the tool list stay empty is a bad way to learn. Try runs it in
 * its own process, reports what it offers, and writes nothing.
 *
 * Environment values are write-only on purpose: they go out to no client, so editing one
 * means typing it again. That is the correct trade for the place an API token lives.
 */
export function MCPServers() {
  const confirm = useConfirm();
  const [servers, setServers] = useState<MCPServer[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState(false);

  const load = useCallback(() => {
    fetchMCPServers()
      .then(setServers)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  useEffect(load, [load]);

  /** Save the whole list — the API replaces it wholesale, which is how a page edits one. */
  const commit = useCallback(
    async (next: MCPServerInput[]) => {
      setBusy(true);
      setError("");
      try {
        const { failed } = await saveMCPServers(next);
        const names = Object.keys(failed);
        // Saved *and* failed to start is the normal case for a server that needs a token, so
        // it is a note rather than a refusal — the configuration is kept either way.
        if (names.length) setError(names.map((n) => `${n}: ${failed[n]}`).join(" · "));
        load();
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  const asInput = (server: MCPServer): MCPServerInput => ({
    label: server.label,
    command: server.command,
    args: server.args,
    // Values are never sent to us, so re-saving a server cannot carry them. The server keeps
    // what it has for a label it already knows; this only ever adds.
    env: {},
    enabled: server.enabled,
  });

  async function toggle(server: MCPServer) {
    if (!servers) return;
    await commit(
      servers.map((s) => (s.label === server.label ? { ...asInput(s), enabled: !s.enabled } : asInput(s))),
    );
  }

  async function remove(server: MCPServer) {
    if (!servers) return;
    const ok = await confirm({
      title: "Remove this server?",
      subject: server.label,
      description: "Its tools stop being offered to him. The program itself is not touched.",
      confirmLabel: "Remove",
    });
    if (ok) await commit(servers.filter((s) => s.label !== server.label).map(asInput));
  }

  async function reconnect() {
    setBusy(true);
    setError("");
    try {
      const { failed } = await reconnectMCPServers();
      const names = Object.keys(failed);
      if (names.length) setError(names.map((n) => `${n}: ${failed[n]}`).join(" · "));
      load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <div className="mb-3 flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold">Other programs' tools</h2>
          <p className="text-muted-foreground text-xs leading-relaxed">
            MCP servers run on this machine and hand him their tools — a database, a
            repository, a design file. Each one's tools cost a little of every message he
            sends, so switch off the ones you are not using rather than removing them.
          </p>
        </div>
        {servers?.length ? (
          <Button size="sm" variant="ghost" onClick={() => void reconnect()} disabled={busy}>
            {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Plug className="size-3.5" />}
            Reconnect
          </Button>
        ) : null}
      </div>

      {error ? (
        <p className="text-destructive mb-3 flex gap-1.5 text-xs leading-relaxed">
          <TriangleAlert className="mt-px size-3.5 shrink-0" />
          <span>{error}</span>
        </p>
      ) : null}

      <div className="divide-y rounded-xl border">
        {servers === null ? (
          <p className="text-muted-foreground p-3 text-sm">Reading…</p>
        ) : servers.length === 0 ? (
          <p className="text-muted-foreground p-3 text-sm">
            None yet. Most servers are a one-line command from their README.
          </p>
        ) : (
          servers.map((server) => (
            <ServerRow
              key={server.label}
              server={server}
              busy={busy}
              onToggle={() => void toggle(server)}
              onRemove={() => void remove(server)}
            />
          ))
        )}
      </div>

      {adding ? (
        <AddServer
          taken={(servers ?? []).map((s) => s.label)}
          onCancel={() => setAdding(false)}
          onAdd={async (server) => {
            await commit([...(servers ?? []).map(asInput), server]);
            setAdding(false);
          }}
        />
      ) : (
        <Button size="sm" variant="outline" className="mt-3" onClick={() => setAdding(true)}>
          <Plus className="size-3.5" />
          Add a server
        </Button>
      )}
    </section>
  );
}

function ServerRow({
  server,
  busy,
  onToggle,
  onRemove,
}: {
  server: MCPServer;
  busy: boolean;
  onToggle: () => void;
  onRemove: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="p-3">
      <div className="flex items-start gap-3">
        {/* Connected is a different question from switched on, and both matter: a server can
            be enabled and dead, which is the state you most need to see. */}
        <span
          className={cn(
            "mt-0.5 shrink-0",
            server.connected ? "text-roam" : "text-muted-foreground/60",
          )}
          title={server.connected ? "Connected" : server.enabled ? "Not running" : "Switched off"}
        >
          {server.connected ? <Check className="size-4" /> : <CircleDashed className="size-4" />}
        </span>
        <div className="min-w-0 flex-1">
          <p className="flex items-baseline gap-2 text-sm font-medium">
            {server.label}
            <span className="text-muted-foreground/60 text-[11px] font-normal">
              {server.connected
                ? `${server.tools.length} tool${server.tools.length === 1 ? "" : "s"}`
                : server.enabled
                  ? "not running"
                  : "off"}
            </span>
          </p>
          <p className="text-muted-foreground/80 mt-0.5 truncate font-mono text-[11px]">
            {[server.command, ...server.args].join(" ")}
          </p>
          {server.envKeys.length ? (
            <p className="text-muted-foreground/60 mt-1 text-[11px]">
              env: {server.envKeys.join(", ")}
            </p>
          ) : null}
          {server.problems.map((problem) => (
            <p key={problem} className="text-destructive mt-1 flex gap-1.5 text-[11px]">
              <TriangleAlert className="mt-px size-3 shrink-0" />
              {problem}
            </p>
          ))}
          {server.connected && server.tools.length ? (
            <button
              type="button"
              onClick={() => setOpen((o) => !o)}
              className="text-muted-foreground/70 hover:text-foreground mt-1.5 text-[11px] underline-offset-2 hover:underline"
            >
              {open ? "Hide its tools" : "What it can do"}
            </button>
          ) : null}
          {open ? (
            <ul className="mt-2 space-y-1">
              {server.tools.map((tool) => (
                <li key={tool.name} className="text-[11px] leading-relaxed">
                  <span className="font-mono">{tool.name}</span>
                  {tool.description ? (
                    <span className="text-muted-foreground"> — {tool.description}</span>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button size="sm" variant="ghost" onClick={onToggle} disabled={busy}>
            {server.enabled ? "Switch off" : "Switch on"}
          </Button>
          <Button
            size="icon-sm"
            variant="ghost"
            className="text-muted-foreground hover:text-destructive"
            onClick={onRemove}
            disabled={busy}
            aria-label={`Remove ${server.label}`}
          >
            <X className="size-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}

function AddServer({
  taken,
  onAdd,
  onCancel,
}: {
  taken: string[];
  onAdd: (server: MCPServerInput) => Promise<void>;
  onCancel: () => void;
}) {
  const [label, setLabel] = useState("");
  const [line, setLine] = useState("");
  const [env, setEnv] = useState("");
  const [probe, setProbe] = useState<MCPProbe | null>(null);
  const [trying, setTrying] = useState(false);

  const [command, ...args] = splitCommand(line);
  const duplicate = taken.includes(label.trim().toLowerCase());
  const ready = Boolean(label.trim() && command) && !duplicate;

  const built = (): MCPServerInput => ({
    label: label.trim().toLowerCase(),
    command,
    args,
    env: parseEnv(env),
    enabled: true,
  });

  async function tryIt() {
    setTrying(true);
    setProbe(null);
    try {
      setProbe(await probeMCPServer(built()));
    } catch (err: unknown) {
      setProbe({ ok: false, detail: err instanceof Error ? err.message : String(err), tools: [] });
    } finally {
      setTrying(false);
    }
  }

  return (
    <div className="mt-3 space-y-3 rounded-xl border p-3">
      <div className="grid gap-3 sm:grid-cols-[10rem_1fr]">
        <Field
          label="Name"
          hint="Becomes part of every tool name it adds."
          value={label}
          onChange={setLabel}
          placeholder="files"
        />
        <Field
          label="Command"
          hint="Paste the line from the server's README."
          value={line}
          onChange={setLine}
          placeholder="npx -y @modelcontextprotocol/server-filesystem ~/Documents"
          mono
        />
      </div>
      <Field
        label="Environment"
        hint="One KEY=value per line. Values are stored and never shown again."
        value={env}
        onChange={setEnv}
        placeholder="GITHUB_TOKEN=ghp_…"
        mono
        lines={2}
      />

      {duplicate ? (
        <p className="text-destructive text-xs">There is already a server called “{label}”.</p>
      ) : null}

      {probe ? (
        <div
          className={cn(
            "rounded-lg border p-2.5 text-xs",
            probe.ok ? "border-roam/40 bg-roam/5" : "border-destructive/40 bg-destructive/5",
          )}
        >
          <p className="font-medium">{probe.ok ? `Answered — ${probe.detail}` : probe.detail}</p>
          {probe.tools.length ? (
            <p className="text-muted-foreground mt-1 leading-relaxed">
              {probe.tools.length} tool{probe.tools.length === 1 ? "" : "s"}:{" "}
              {probe.tools.map((t) => t.name).join(", ")}
            </p>
          ) : null}
        </div>
      ) : null}

      <div className="flex items-center gap-2">
        {/* Try first, deliberately. A server that does not start is the common case, and
            finding out by saving it and watching the tool list stay empty teaches nothing. */}
        <Button size="sm" variant="outline" onClick={() => void tryIt()} disabled={!ready || trying}>
          {trying ? <Loader2 className="size-3.5 animate-spin" /> : null}
          {trying ? "Trying…" : "Try it"}
        </Button>
        <Button size="sm" onClick={() => void onAdd(built())} disabled={!ready}>
          Add
        </Button>
        <div className="flex-1" />
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

function Field({
  label,
  hint,
  value,
  onChange,
  placeholder,
  mono,
  lines,
}: {
  label: string;
  hint: string;
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  mono?: boolean;
  lines?: number;
}) {
  const shared = cn(
    "border-border/60 bg-card/40 focus-visible:border-ring w-full rounded-lg border px-2.5 py-1.5 text-xs outline-none",
    mono && "font-mono",
  );
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium">{label}</span>
      {lines ? (
        <textarea
          rows={lines}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
          className={cn(shared, "resize-y")}
        />
      ) : (
        <input
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
          className={shared}
        />
      )}
      <span className="text-muted-foreground/70 mt-1 block text-[11px]">{hint}</span>
    </label>
  );
}

/** Split a pasted command line, respecting quoted arguments.
 *
 * `npx -y @scope/server "/Users/me/My Documents"` is one server and three arguments, and
 * splitting on whitespace alone would turn the last one into two directories that do not
 * exist — a failure that reads as "the server is broken". */
function splitCommand(line: string): string[] {
  const out: string[] = [];
  const pattern = /"([^"]*)"|'([^']*)'|(\S+)/g;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(line)) !== null) {
    out.push(match[1] ?? match[2] ?? match[3]);
  }
  return out;
}

/** `KEY=value` lines to an object, ignoring blanks and comments. */
function parseEnv(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const at = line.indexOf("=");
    if (at <= 0) continue;
    out[line.slice(0, at).trim()] = line.slice(at + 1).trim();
  }
  return out;
}
