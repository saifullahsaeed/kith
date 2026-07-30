import { useState } from "react";
import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { patchServerConfig, type ConnectionState, type ServerConfig } from "@/lib/backend";

const inputClass =
  "w-full rounded-md border bg-transparent px-3 py-2 text-sm shadow-xs outline-none transition-colors focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40";

/**
 * How he thinks, as opposed to where — reply length, reasoning, and who he is.
 *
 * Separate from the Model tab because these are the settings that outlive a model:
 * swapping providers shouldn't disturb the persona, and editing the persona shouldn't
 * make anyone re-validate a key. They were all one dialog before, which meant every
 * change went through a single Save and touched everything.
 *
 * The context window is shown only for a local model, because that is the only place
 * it does anything: `num_ctx` goes to Ollama and is absent from the OpenAI-compatible
 * request entirely. Offering it to a cloud user would be a control that silently does
 * nothing, over a number that is in fact a fixed property of the model they chose.
 * Locally it is the opposite — among the most consequential settings here, since Ollama
 * picks a small window unless told otherwise and then truncates without saying so.
 */
export function ChatTab({
  config,
  connection,
  serverDefaults,
  onSave,
}: {
  config: ServerConfig;
  connection: ConnectionState;
  serverDefaults: ServerConfig;
  onSave: (config: ServerConfig) => void;
}) {
  const local = connection.kind === "ollama";
  const [draft, setDraft] = useState(config);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");

  const dirty =
    (local && draft.numCtx !== config.numCtx) ||
    draft.numPredict !== config.numPredict ||
    draft.think !== config.think ||
    draft.system !== config.system;

  function edit(patch: Partial<ServerConfig>) {
    setDraft((current) => ({ ...current, ...patch }));
    setSaved(false);
    setError("");
  }

  async function save() {
    setSaving(true);
    setError("");
    try {
      const server = await patchServerConfig({
        // Sent only where it applies, so a cloud user's stored value is never
        // rewritten by a page that wasn't showing it to them.
        ...(local ? { numCtx: positiveOr(draft.numCtx, config.numCtx) } : {}),
        numPredict: predictOr(draft.numPredict, config.numPredict),
        think: draft.think,
      });
      // The persona stays a local override; everything else comes back from the server.
      onSave({ ...server, system: draft.system });
      setSaved(true);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-6">
      <section>
        <div className="mb-3">
          <h2 className="text-sm font-semibold">{local ? "Room to think" : "Reply length"}</h2>
          <p className="text-muted-foreground text-xs">
            {local
              ? "Ollama picks a small window unless you set one, then truncates without " +
                "saying so. A tick can run sixteen rounds carrying tool output, so this is " +
                "what keeps him coherent to the end of one."
              : "His context window comes from the model itself — see the Model tab. " +
                "There's nothing to set here."}
          </p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          {local ? (
            <Field label="Context window (tokens)">
              <input
                type="number"
                className={inputClass}
                value={draft.numCtx}
                onChange={(event) => edit({ numCtx: Number(event.target.value) })}
              />
            </Field>
          ) : null}
          <Field label="Longest single reply (-1 lets the model decide)">
            <input
              type="number"
              className={inputClass}
              value={draft.numPredict}
              onChange={(event) => edit({ numPredict: Number(event.target.value) })}
            />
          </Field>
        </div>
        <label className="mt-4 flex cursor-pointer items-start gap-3">
          <input
            type="checkbox"
            className="mt-0.5 size-4 accent-[var(--kith)]"
            checked={draft.think}
            onChange={(event) => edit({ think: event.target.checked })}
          />
          <span>
            <span className="block text-sm font-medium">Let him reason before answering</span>
            <span className="text-muted-foreground block text-xs">
              Slower and costs more tokens. On a model that reasons well, it's the difference
              between a plan and a guess.
            </span>
          </span>
        </label>
      </section>

      <section>
        <div className="mb-3 flex items-baseline gap-3">
          <h2 className="text-sm font-semibold">Who he is</h2>
          <p className="text-muted-foreground min-w-0 flex-1 text-xs">
            His persona, merged from the <code>persona/</code> folder on the server.
          </p>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => edit({ system: serverDefaults.system })}
            disabled={draft.system === serverDefaults.system}
          >
            Reset to his own
          </Button>
        </div>
        <textarea
          rows={12}
          className={`${inputClass} resize-y font-mono text-xs leading-relaxed`}
          value={draft.system}
          onChange={(event) => edit({ system: event.target.value })}
        />
      </section>

      {error ? <p className="text-destructive text-sm">{error}</p> : null}

      <div className="flex items-center gap-3 border-t pt-4">
        <span className="text-muted-foreground text-xs">
          {saved && !dirty ? "Saved." : dirty ? "Unsaved change." : "Nothing to save."}
        </span>
        <div className="flex-1" />
        <Button onClick={save} disabled={!dirty || saving}>
          {saving ? <Loader2 className="size-4 animate-spin" /> : null}
          {saving ? "Saving…" : "Save"}
        </Button>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="grid gap-1.5">
      <span className="text-muted-foreground text-xs">{label}</span>
      {children}
    </label>
  );
}

function positiveOr(value: number, fallback: number): number {
  return Number.isInteger(value) && value > 0 ? value : fallback;
}

function predictOr(value: number, fallback: number): number {
  return Number.isInteger(value) && (value > 0 || value === -1) ? value : fallback;
}
