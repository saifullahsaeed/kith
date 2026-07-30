import { useCallback, useEffect, useState } from "react";
import { FolderOpen, Loader2, RotateCcw, TerminalSquare } from "lucide-react";

import { Button } from "@/components/ui/button";
import { openOnHost } from "@/lib/files";
import {
  fetchTuning,
  resetTuning,
  saveTuning,
  type Tunable,
  type TuningSnapshot,
} from "@/lib/backend";

const inputClass =
  "w-full rounded-md border bg-transparent px-3 py-1.5 text-right font-mono text-sm shadow-xs outline-none transition-colors focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40 disabled:cursor-not-allowed disabled:opacity-50";

/**
 * Everything else, with the consequences written down.
 *
 * These were environment variables and inlined constants — which for a packaged
 * desktop app means unreachable, since there is no shell to export anything in. If
 * this is going to be something other people run, "you can configure it by editing
 * the source" is not configurable.
 *
 * Every field, its label, its bounds and its explanation come from the server's
 * registry, so this component knows nothing about any particular setting and adding
 * one needs no change here. The help text is always visible rather than hidden behind
 * a tooltip: these are sharp, and a number whose consequence you have to hover to
 * discover is a trap.
 */
export function AdvancedTab() {
  const [snapshot, setSnapshot] = useState<TuningSnapshot | null>(null);
  const [draft, setDraft] = useState<Record<string, number | string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    fetchTuning()
      .then((next) => {
        setSnapshot(next);
        setDraft({});
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  useEffect(load, [load]);

  const dirty = Object.keys(draft).length > 0;

  async function commit(action: () => Promise<TuningSnapshot>) {
    setBusy(true);
    setError("");
    try {
      setSnapshot(await action());
      setDraft({});
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (error && !snapshot) return <p className="text-destructive text-sm">{error}</p>;
  if (!snapshot) {
    return (
      <p className="text-muted-foreground flex items-center gap-2 text-sm">
        <Loader2 className="size-4 animate-spin" /> Reading his settings…
      </p>
    );
  }

  return (
    <div className="space-y-8">
      <p className="text-muted-foreground text-xs leading-relaxed">
        Every one of these changes how he behaves, and takes effect on his next turn — nothing here
        needs a restart. Each says what it costs you to get wrong.
      </p>

      {snapshot.groups.map((group) => (
        <section key={group.key}>
          <div className="mb-3">
            <h2 className="text-sm font-semibold">{group.label}</h2>
            <p className="text-muted-foreground text-xs leading-relaxed">{group.blurb}</p>
          </div>
          <div className="divide-y rounded-xl border">
            {group.settings.map((knob) => (
              <Row
                key={knob.key}
                knob={knob}
                draft={draft[knob.key]}
                onChange={(value) =>
                  setDraft((current) => {
                    // Typing a value back to what's stored isn't a change, so the Save
                    // button shouldn't claim there's something to save.
                    const next = { ...current };
                    if (value === "" || value === knob.value) delete next[knob.key];
                    else next[knob.key] = value;
                    return next;
                  })
                }
                onReset={() => commit(() => resetTuning([knob.key]))}
              />
            ))}
          </div>
        </section>
      ))}

      <section>
        <div className="mb-3 flex items-baseline gap-3">
          <h2 className="text-sm font-semibold">On this computer</h2>
          <p className="text-muted-foreground min-w-0 flex-1 text-xs">
            He works in real folders now, not a container. These are the ones, with what is in them
            — clickable, because a path you can only read is a path you have to retype.
          </p>
          <span className="text-muted-foreground/70 font-mono text-[11px] tabular-nums">
            {formatBytes(snapshot.paths.reduce((sum, path) => sum + (path.bytes ?? 0), 0))} total
          </span>
        </div>
        <div className="divide-y rounded-xl border">
          {snapshot.paths.map((path) => (
            <div key={path.label} className="flex items-start gap-3 p-3">
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2">
                  <span className="text-sm font-medium">{path.label}</span>
                  {path.bytes !== undefined ? (
                    <span className="text-muted-foreground/70 font-mono text-[10px] tabular-nums">
                      {formatBytes(path.bytes)}
                      {path.entries !== undefined ? ` · ${path.entries} items` : ""}
                    </span>
                  ) : null}
                  {path.env ? (
                    <code className="text-muted-foreground/50 ms-auto font-mono text-[10px]">
                      {path.env}
                    </code>
                  ) : null}
                </div>
                <code className="text-muted-foreground mt-0.5 block font-mono text-[11px] break-all">
                  {path.value}
                </code>
                {path.note ? (
                  <p className="text-muted-foreground/70 mt-0.5 text-[11px]">{path.note}</p>
                ) : null}
              </div>
              {path.open ? (
                <Button
                  variant="outline"
                  size="sm"
                  className="shrink-0"
                  onClick={() => void openOnHost(path.value, true)}
                  title="Show it in Finder"
                >
                  <FolderOpen className="size-3.5" />
                  Reveal
                </Button>
              ) : null}
            </div>
          ))}
        </div>
      </section>

      {error ? <p className="text-destructive text-sm">{error}</p> : null}

      <div className="sticky bottom-0 -mx-6 flex items-center gap-3 border-t bg-background/95 px-6 py-3 backdrop-blur">
        <Button
          variant="ghost"
          size="sm"
          className="text-muted-foreground"
          onClick={() => commit(() => resetTuning())}
          disabled={busy}
        >
          <RotateCcw className="size-3.5" /> Reset everything
        </Button>
        <div className="flex-1" />
        <span className="text-muted-foreground text-xs">
          {dirty ? `${Object.keys(draft).length} changed` : "Nothing to save."}
        </span>
        <Button onClick={() => commit(() => saveTuning(draft))} disabled={!dirty || busy}>
          {busy ? <Loader2 className="size-4 animate-spin" /> : null}
          {busy ? "Saving…" : "Save"}
        </Button>
      </div>
    </div>
  );
}

function Row({
  knob,
  draft,
  onChange,
  onReset,
}: {
  knob: Tunable;
  draft: number | string | undefined;
  onChange: (value: number | string) => void;
  onReset: () => void;
}) {
  const shown = draft ?? knob.value;
  const changed = draft !== undefined;

  return (
    <div className="flex flex-wrap items-start gap-x-4 gap-y-2 p-3">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="text-sm font-medium">{knob.label}</span>
          {!knob.isDefault && !knob.fromEnv ? (
            <button
              type="button"
              onClick={onReset}
              className="text-muted-foreground/70 hover:text-kith text-[10px] underline decoration-dotted"
              title={`Back to ${knob.default}`}
            >
              changed — reset
            </button>
          ) : null}
          {knob.fromEnv ? (
            <span
              className="text-muted-foreground/70 inline-flex items-center gap-1 text-[10px]"
              title={`${knob.env} is set in the environment, so it wins over anything saved here.`}
            >
              <TerminalSquare className="size-3" /> set by {knob.env}
            </span>
          ) : null}
        </div>
        <p className="text-muted-foreground mt-0.5 text-xs leading-relaxed">{knob.help}</p>
      </div>

      <label className="flex shrink-0 items-center gap-2">
        <span className="w-32">
          <input
            type={knob.kind === "text" ? "text" : "number"}
            step={knob.kind === "float" ? 0.05 : 1}
            min={knob.min ?? undefined}
            max={knob.max ?? undefined}
            className={`${inputClass} ${changed ? "border-kith" : ""} ${knob.kind === "text" ? "text-left" : ""}`}
            value={String(shown)}
            disabled={knob.fromEnv}
            placeholder={String(knob.default)}
            spellCheck={false}
            onChange={(event) => onChange(event.target.value)}
            aria-label={knob.label}
          />
        </span>
        <span className="text-muted-foreground/70 w-16 text-[11px]">{knob.unit}</span>
      </label>
    </div>
  );
}

/** Bytes at the coarseness a person reads: nobody wants 1,721,233. */
function formatBytes(count: number): string {
  if (!count) return "0 B";
  if (count < 1024) return `${count} B`;
  if (count < 1024 * 1024) return `${(count / 1024).toFixed(0)} KB`;
  if (count < 1024 * 1024 * 1024) return `${(count / 1024 / 1024).toFixed(1)} MB`;
  return `${(count / 1024 / 1024 / 1024).toFixed(2)} GB`;
}
