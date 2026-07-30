import { useState, type ReactNode } from "react";
import { Cloud, Laptop, Settings2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { patchServerConfig, type ServerConfig } from "@/lib/backend";

const inputClass =
  "w-full rounded-md border bg-transparent px-3 py-2 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40";

/** Edits the chat config. Values are sent to the server per message and saved
 * in the browser; "Reset persona" restores the server's default persona. */
export function SettingsDialog({
  config,
  serverDefaults,
  onSave,
}: {
  config: ServerConfig;
  serverDefaults: ServerConfig;
  onSave: (config: ServerConfig) => void;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(config);
  const [apiKey, setApiKey] = useState(""); // write-only; blank = leave existing key
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const cloudOn = Boolean(draft.baseUrl && (draft.apiKeySet || apiKey.trim()));

  function handleOpenChange(next: boolean) {
    if (next) {
      setDraft(config);
      setApiKey("");
      setError("");
    }
    setOpen(next);
  }

  async function save() {
    setSaving(true);
    setError("");
    const model = draft.model.trim() || config.model;
    const numCtx = positiveOr(draft.numCtx, config.numCtx);
    const numPredict = predictOr(draft.numPredict, config.numPredict);
    const baseUrl = draft.baseUrl.trim();
    try {
      // Persist to the server so autonomy uses it too; key is write-only.
      const server = await patchServerConfig({
        model,
        numCtx,
        numPredict,
        think: draft.think,
        baseUrl,
        ...(apiKey.trim() ? { apiKey: apiKey.trim() } : {}),
      });
      // Keep the local persona override; take everything else from the server.
      onSave({ ...server, system: draft.system });
      setOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          className="text-muted-foreground hover:text-foreground"
          aria-label="Settings"
        >
          <Settings2 className="size-4" />
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Settings</DialogTitle>
          <DialogDescription>Model, cloud provider, and persona. Saved on the server.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-1">
          <div className="rounded-lg border p-3">
            <div className="mb-2 flex items-center gap-2 text-sm font-medium">
              {cloudOn ? <Cloud className="size-4 text-kith" /> : <Laptop className="size-4 text-muted-foreground" />}
              Where he thinks
              <span className={`ml-auto text-xs ${cloudOn ? "text-kith" : "text-muted-foreground"}`}>
                {cloudOn ? "cloud (fan off)" : "local Ollama"}
              </span>
            </div>
            <Field label="Cloud endpoint — base URL (blank = local Ollama)">
              <input
                className={inputClass}
                placeholder="https://openrouter.ai/api/v1"
                value={draft.baseUrl}
                onChange={(e) => setDraft({ ...draft, baseUrl: e.target.value })}
              />
            </Field>
            <div className="mt-3">
              <Field label={draft.apiKeySet ? "API key (a key is stored — type to replace)" : "API key"}>
                <input
                  type="password"
                  autoComplete="off"
                  className={inputClass}
                  placeholder={draft.apiKeySet ? "•••••••• stored" : "paste your cloud API key"}
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                />
              </Field>
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              OpenAI-compatible. e.g. OpenRouter <code>https://openrouter.ai/api/v1</code> + model
              <code> deepseek/deepseek-chat</code>, or NVIDIA <code>https://integrate.api.nvidia.com/v1</code>.
            </p>
          </div>
          <Field label="Model">
            <input
              className={inputClass}
              value={draft.model}
              onChange={(e) => setDraft({ ...draft, model: e.target.value })}
            />
          </Field>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Context (num_ctx)">
              <input
                type="number"
                className={inputClass}
                value={draft.numCtx}
                onChange={(e) => setDraft({ ...draft, numCtx: Number(e.target.value) })}
              />
            </Field>
            <Field label="Max output (-1 = ∞)">
              <input
                type="number"
                className={inputClass}
                value={draft.numPredict}
                onChange={(e) => setDraft({ ...draft, numPredict: Number(e.target.value) })}
              />
            </Field>
          </div>
          <Field label="System prompt (persona)">
            <textarea
              rows={6}
              className={`${inputClass} resize-y`}
              value={draft.system}
              onChange={(e) => setDraft({ ...draft, system: e.target.value })}
            />
          </Field>
        </div>
        {error ? <p className="text-sm text-destructive">{error}</p> : null}
        <DialogFooter className="sm:justify-between">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setDraft({ ...draft, system: serverDefaults.system })}
          >
            Reset persona
          </Button>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => setOpen(false)} disabled={saving}>
              Cancel
            </Button>
            <Button onClick={save} disabled={saving}>{saving ? "Saving…" : "Save"}</Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="grid gap-1.5">
      <span className="text-xs text-muted-foreground">{label}</span>
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
