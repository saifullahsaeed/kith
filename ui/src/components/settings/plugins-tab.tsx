import { useCallback, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CircleAlert, FolderOpen, PlugZap, Puzzle, Trash2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { inputClass } from "@/components/ui/input";
import { useConfirm } from "@/components/ui/confirm";
import { useLayout } from "@/components/shell/layout/store";
import {
  fetchPlugins,
  installPlugin,
  patchPlugin,
  removePlugin,
  reviewPlugin,
  type Plugin,
  type PluginReview,
  type PluginsSnapshot,
} from "@/lib/backend";
import { keys } from "@/lib/query-keys";

/**
 * What is installed, what it costs, and whether it is working.
 *
 * **The review is a separate step and that is the point.** `reviewPlugin` parses a folder,
 * prices what it would add to every request, and writes nothing — the same rule the MCP screen
 * states as *trying is not saving*. So a folder that turns out to be wrong does not leave
 * anything installed, and the recurring cost is on screen before anyone agrees to it rather
 * than discoverable afterwards in the context breakdown.
 *
 * Every string a plugin contributes is rendered as **data** — monospace, in its own field, with
 * the plugin's name beside it. None of it is folded into a sentence this app speaks.
 * `permissions._refuse` is explicit that a justification is "only ever supplied in code, never
 * from anything a model composed"; a third party's sentence on the screen where somebody grants
 * disk access is that mistake one layer out.
 */
export function PluginsTab() {
  const { data, refetch } = useQuery({ queryKey: keys.plugins(), queryFn: fetchPlugins });
  const [busy, setBusy] = useState("");
  const [failure, setFailure] = useState("");

  const act = useCallback(
    async (what: string, run: () => Promise<PluginsSnapshot>) => {
      setBusy(what);
      setFailure("");
      try {
        await run();
        await refetch();
      } catch (err: unknown) {
        setFailure(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy("");
      }
    },
    [refetch],
  );

  if (!data) return null;

  const spent = data.promptTokens;
  const share = data.promptLimit ? Math.round((data.promptChars / data.promptLimit) * 100) : 0;

  return (
    <div className="space-y-7">
      <Install onInstalled={() => void refetch()} busy={!!busy} />

      {failure ? (
        <p className="text-destructive flex items-start gap-1.5 text-[12px]">
          <CircleAlert className="mt-0.5 size-3.5 shrink-0" />
          {failure}
        </p>
      ) : null}

      <section className="space-y-3">
        <header className="flex items-baseline justify-between gap-3">
          <h3 className="text-sm font-medium">Installed</h3>
          {/* The one figure that decides whether installing another is free. Same shape the
              Skills screen uses for its index, and the same reason: this rides in the cached
              prefix on every single request. */}
          <p className="text-muted-foreground/70 text-[11px]">
            {data.plugins.length
              ? `~${spent.toLocaleString()} tokens on every request · ${share}% of the limit`
              : "nothing installed"}
          </p>
        </header>

        {data.plugins.length === 0 ? (
          <p className="text-muted-foreground text-[12px]">
            A plugin is one folder with a <code className="font-mono">kith.plugin.json</code> in
            it. There is a worked example in{" "}
            <code className="font-mono">examples/plugins/sketchpad</code>.
          </p>
        ) : (
          <ul className="divide-border/60 divide-y">
            {data.plugins.map((plugin) => (
              <Row
                key={plugin.id}
                plugin={plugin}
                state={data.state.find((one) => one.plugin === plugin.id)}
                busy={busy === plugin.id}
                onToggle={(on) => act(plugin.id, () => patchPlugin(plugin.id, { enabled: on }))}
                onDigest={(on) => act(plugin.id, () => patchPlugin(plugin.id, { digest: on }))}
                onRemove={(deleteData) =>
                  act(plugin.id, () => removePlugin(plugin.id, deleteData))
                }
              />
            ))}
          </ul>
        )}
      </section>

      {data.problems.length ? (
        <section className="space-y-2">
          <h3 className="text-sm font-medium">Not working</h3>
          {/* A plugin has up to five independently-failing parts, and before this screen the
              diagnostic path for "it does nothing" was to guess. */}
          <ul className="space-y-1.5">
            {data.problems.map((trouble) => (
              <li key={`${trouble.kind}:${trouble.id}`} className="text-[12px]">
                <code className="font-mono">{trouble.id}</code>
                <span className="text-muted-foreground/60"> · {trouble.kind}</span>
                <p className="text-muted-foreground">{trouble.error}</p>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

/** Point at a folder, read what it would mean, then agree to it. */
function Install({ onInstalled, busy }: { onInstalled: () => void; busy: boolean }) {
  const [path, setPath] = useState("");
  const [review, setReview] = useState<PluginReview | null>(null);
  const [failure, setFailure] = useState("");
  const [working, setWorking] = useState(false);

  const look = async () => {
    setWorking(true);
    setFailure("");
    setReview(null);
    try {
      setReview(await reviewPlugin(path.trim()));
    } catch (err: unknown) {
      setFailure(err instanceof Error ? err.message : String(err));
    } finally {
      setWorking(false);
    }
  };

  const install = async () => {
    setWorking(true);
    try {
      await installPlugin(path.trim());
      setReview(null);
      setPath("");
      onInstalled();
    } catch (err: unknown) {
      setFailure(err instanceof Error ? err.message : String(err));
    } finally {
      setWorking(false);
    }
  };

  return (
    <section className="space-y-2.5">
      <h3 className="text-sm font-medium">Install one</h3>
      <div className="flex gap-2">
        <input
          value={path}
          onChange={(event) => setPath(event.target.value)}
          placeholder="/path/to/the/plugin/folder"
          className={`${inputClass} flex-1 font-mono text-[12px]`}
          onKeyDown={(event) => {
            if (event.key === "Enter" && path.trim()) void look();
          }}
        />
        <Button variant="outline" onClick={() => void look()} disabled={!path.trim() || working}>
          <FolderOpen className="size-3.5" />
          Read it
        </Button>
      </div>

      {failure ? <p className="text-destructive text-[12px]">{failure}</p> : null}

      {review ? <Review review={review} busy={working || busy} onInstall={() => void install()} /> : null}
    </section>
  );
}

/**
 * What installing this would mean, before it happens.
 *
 * **Counts, not capabilities**, and the *sealed* side is stated as plainly as the seen side.
 * A boundary is defined by both halves, which makes an over-broad request argue against itself.
 * And the last line is the app's own voice, unconditional: a confined plugin can still send
 * everything it can read anywhere it likes, and the screen says so rather than saying
 * "sandboxed".
 */
function Review({
  review,
  busy,
  onInstall,
}: {
  review: PluginReview;
  busy: boolean;
  onInstall: () => void;
}) {
  const plugin = review.plugin;
  const server = plugin.server;
  return (
    <div className="border-border/70 bg-card/50 space-y-3 rounded-lg border p-3">
      <div className="flex items-baseline gap-2">
        <Puzzle className="text-muted-foreground/60 size-3.5 shrink-0 self-center" />
        <span className="text-sm font-medium">{plugin.name}</span>
        <code className="text-muted-foreground/70 font-mono text-[11px]">
          {plugin.id} {plugin.version}
        </code>
      </div>

      <dl className="space-y-1.5 text-[12px]">
        <Fact label="Adds to every request">
          ~{review.promptTokens.toLocaleString()} tokens, for as long as it is installed
        </Fact>
        {plugin.surfaces.length ? (
          <Fact label="Tabs">{plugin.surfaces.map((one) => one.title).join(", ")}</Fact>
        ) : null}
        {plugin.commands.length ? (
          <Fact label="He can call">
            {plugin.commands.filter((one) => one.model).map((one) => one.title).join(", ") ||
              "nothing — its commands are buttons only"}
          </Fact>
        ) : null}
        {plugin.skills.length ? <Fact label="Skills">{plugin.skills.join(", ")}</Fact> : null}
        {server ? (
          <>
            <Fact label="Runs a program">
              <code className="font-mono">
                {server.command} {server.args.join(" ")}
              </code>
            </Fact>
            <Fact label="It will see">
              {server.reach.read.length ? server.reach.read.join(", ") : "only its own storage"}
            </Fact>
            <Fact label="Sealed from it">
              the rest of your home folder, your keys and credentials, Kith's own database
            </Fact>
            {server.envKeys.length ? (
              <Fact label="Wants credentials">
                <code className="font-mono">{server.envKeys.join(", ")}</code>
              </Fact>
            ) : null}
          </>
        ) : (
          <Fact label="Runs a program">no — this plugin runs nothing of its own</Fact>
        )}
      </dl>

      {server ? (
        <p className="text-muted-foreground border-border/50 border-t pt-2.5 text-[12px]">
          {plugin.name} will be able to see those files, and{" "}
          {server.reach.network ? "could send them anywhere" : "has no network access"}.
        </p>
      ) : null}

      {plugin.unsupportedFields.length ? (
        <p className="text-muted-foreground/70 text-[11px]">
          Expects features this Kith does not have:{" "}
          <code className="font-mono">{plugin.unsupportedFields.join(", ")}</code>
        </p>
      ) : null}

      {review.faults.length ? (
        <ul className="text-destructive space-y-1 text-[12px]">
          {review.faults.map((fault) => (
            <li key={fault}>{fault}</li>
          ))}
        </ul>
      ) : (
        <Button onClick={onInstall} disabled={busy} size="sm">
          Allow and install
        </Button>
      )}
    </div>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2">
      <dt className="text-muted-foreground/70 w-40 shrink-0">{label}</dt>
      <dd className="min-w-0 flex-1 break-words">{children}</dd>
    </div>
  );
}

/** One installed plugin: what it offers, what you decided, and whether it is working. */
function Row({
  plugin,
  state,
  busy,
  onToggle,
  onDigest,
  onRemove,
}: {
  plugin: Plugin;
  state?: { keys: number; bytes: number; digestOn: boolean; digestLine: string };
  busy: boolean;
  onToggle: (on: boolean) => void;
  onDigest: (on: boolean) => void;
  onRemove: (deleteData: boolean) => void;
}) {
  const open = useLayout((one) => one.open);
  const confirm = useConfirm();
  const offersDigest = !!(plugin.state as { digest?: unknown })?.digest;

  return (
    <li className="space-y-2 py-3">
      <div className="flex items-start gap-2.5">
        <Puzzle className="text-muted-foreground/50 mt-0.5 size-3.5 shrink-0" />
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className="text-[13px] font-medium">{plugin.name}</span>
            <code className="text-muted-foreground/60 font-mono text-[10px]">
              {plugin.version}
            </code>
            {!plugin.decided.enabled ? (
              <span className="text-muted-foreground/60 text-[10px]">switched off</span>
            ) : null}
          </div>
          {plugin.description ? (
            <p className="text-muted-foreground text-[12px]">{plugin.description}</p>
          ) : null}
          <p className="text-muted-foreground/60 mt-0.5 text-[11px]">
            ~{plugin.promptTokens.toLocaleString()} tokens per request
            {plugin.skills.length ? ` · ${plugin.skills.length} skill(s)` : ""}
            {plugin.server ? " · runs a program" : ""}
            {state ? ` · holding ${state.keys} key(s)` : ""}
          </p>
        </div>

        <button
          type="button"
          onClick={() => onRemove(false)}
          disabled={busy}
          title="Remove it — its stored data is kept for thirty days"
          aria-label={`Remove ${plugin.name}`}
          className="text-muted-foreground/40 hover:text-destructive focus-visible:text-destructive mt-0.5 shrink-0 transition"
        >
          <X className="size-3.5" />
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-2 pl-6">
        {/* Opening its tab. There is no other way to reach a plugin surface yet: a plugin's own
            `open_surface` command is `host` delivery, which is not built, so without this button
            a tab could only appear by having been open before a reload. */}
        {plugin.surfaces.map((surface) => (
          <Button
            key={surface.view}
            variant="outline"
            size="sm"
            disabled={!plugin.decided.enabled}
            onClick={() => open({ surface: "plugin", plugin: plugin.id, view: surface.view })}
            className="gap-1.5 text-[11px]"
          >
            <PlugZap className="size-3" />
            Open {surface.title}
          </Button>
        ))}

        <Button
          variant="outline"
          size="sm"
          disabled={busy}
          onClick={() => onToggle(!plugin.decided.enabled)}
          className="text-[11px]"
        >
          {plugin.decided.enabled ? "Switch off" : "Switch on"}
        </Button>

        {offersDigest ? (
          <label className="text-muted-foreground flex cursor-pointer items-center gap-1.5 text-[11px] select-none">
            <input
              type="checkbox"
              checked={plugin.decided.digest}
              disabled={busy}
              onChange={(event) => onDigest(event.target.checked)}
              className="accent-kith size-3.5"
            />
            {/* The only thing here that spends tokens on every turn forever, which is why a
                manifest may offer one and only a person may switch it on. */}
            Tell him what it is holding
          </label>
        ) : null}

        <Button
          variant="ghost"
          size="sm"
          disabled={busy}
          onClick={async () => {
            const sure = await confirm({
              title: "Delete its stored data too?",
              subject: plugin.name,
              description:
                state && state.keys
                  ? `It is holding ${state.keys} key(s), ${state.bytes.toLocaleString()} bytes. Removing without this keeps them for thirty days, so a reinstall gets them back.`
                  : "It is holding nothing, so there is nothing to lose.",
              confirmLabel: "Remove and delete",
            });
            if (sure) onRemove(true);
          }}
          className="text-muted-foreground/60 hover:text-destructive gap-1.5 text-[11px]"
        >
          <Trash2 className="size-3" />
          Remove and delete its data
        </Button>
      </div>

      {/* The digest line, verbatim. The highest-value item on this screen: it answers "why does
          he not know about my state" in one glance, where every other signal only says that
          something was written. */}
      {state?.digestOn ? (
        <p className="text-muted-foreground/70 pl-6 font-mono text-[11px]">
          {state.digestLine || "— nothing to tell him yet"}
        </p>
      ) : null}

      {plugin.problems.length ? (
        <ul className="text-destructive space-y-1 pl-6 text-[12px]">
          {plugin.problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}
