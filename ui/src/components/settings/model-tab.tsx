import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ConnectStep } from "@/components/onboarding/connect-step";
import { ModelStep } from "@/components/onboarding/model-step";
import { ProviderChoice } from "@/components/onboarding/provider-choice";
import { useConnectionProbe } from "@/hooks/use-connection-probe";
import {
  completeSetup,
  type ConnectionState,
  type ProviderCard,
  type ProviderKind,
} from "@/lib/backend";

/**
 * Where he thinks — changed after the fact rather than during onboarding.
 *
 * Built from the same three components onboarding uses, because the decision is
 * identical and a second implementation of it would drift. What differs is the frame:
 * no wizard, no forward motion, and the current setup is shown as the starting point
 * so the common case (swap the model, keep the key) is two clicks.
 *
 * Switching provider is deliberately a separate act from editing a model. It clears
 * the credential — a key for one endpoint means nothing at another — so it should not
 * be something you fall into by mis-clicking a card.
 */
export function ModelTab({
  connection,
  providers,
  onSaved,
}: {
  connection: ConnectionState;
  providers: ProviderCard[];
  onSaved: () => void;
}) {
  const [kind, setKind] = useState<ProviderKind>(connection.kind);
  const [switching, setSwitching] = useState(false);
  const [model, setModel] = useState(connection.model ?? "");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");

  const provider = providers.find((entry) => entry.kind === kind) ?? null;
  const probe = useConnectionProbe({ provider });
  // Pre-fill the endpoint when editing the provider already in use, so a probe runs
  // immediately and the model list is there without anyone typing anything.
  useEffect(() => {
    if (kind === connection.kind && connection.baseUrl) probe.setBaseUrl(connection.baseUrl);
    // Only on a provider change; probe.setBaseUrl is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, connection.kind, connection.baseUrl]);

  // A pasted key is a change on its own. Leaving it out meant the only way to save a
  // new key was to also change the model or the provider — which is what someone was
  // reduced to doing.
  const keyEdited = probe.apiKey.trim().length > 0;
  const urlEdited = probe.baseUrl.trim() !== (connection.baseUrl ?? "");
  const dirty =
    model !== (connection.model ?? "") || kind !== connection.kind || keyEdited || urlEdited;

  async function save() {
    setSaving(true);
    setError("");
    try {
      await completeSetup({
        kind,
        baseUrl: probe.baseUrl.trim() || undefined,
        apiKey: probe.apiKey.trim() || undefined,
        model,
      });
      setSaved(true);
      // Drop the plaintext now it's stored: the form goes back to a locked field, and
      // the key stops sitting in component state for the rest of the session.
      probe.setApiKey("");
      onSaved();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-6">
      <Section
        title="Provider"
        hint={
          switching
            ? "Choosing a different one clears the key you have saved."
            : `He thinks through ${provider?.label ?? connection.kind}.`
        }
        action={
          <Button variant="outline" size="sm" onClick={() => setSwitching((open) => !open)}>
            {switching ? "Keep it" : "Change provider"}
          </Button>
        }
      >
        {switching ? (
          <ProviderChoice
            providers={providers}
            onChoose={(next) => {
              setKind(next);
              setModel("");
              probe.reset();
              setSwitching(false);
            }}
          />
        ) : null}
      </Section>

      {provider ? (
        <Section title="Connection">
          <ConnectStep
            provider={provider}
            baseUrl={probe.baseUrl}
            apiKey={probe.apiKey}
            keyStored={connection.apiKeySet && connection.kind === provider.kind}
            probe={probe.probe}
            probing={probe.probing}
            onBaseUrl={probe.setBaseUrl}
            onApiKey={probe.setApiKey}
            // There is nowhere to continue to here; Enter should just not submit.
            onContinue={() => {}}
          />
        </Section>
      ) : null}

      {probe.probe.models.length > 0 ? (
        <Section title="Model">
          <ModelStep
            models={probe.probe.models}
            suggested={probe.probe.suggested}
            selected={model}
            onSelect={(id) => {
              setModel(id);
              setSaved(false);
              setError("");
            }}
          />
        </Section>
      ) : null}

      {error ? <p className="text-destructive text-sm">{error}</p> : null}

      <div className="flex items-center gap-3 border-t pt-4">
        <span className="text-muted-foreground text-xs">
          {saved && !dirty
            ? "Saved. He'll use it on his next message."
            : keyEdited && model === (connection.model ?? "")
              ? "New key — save to use it."
              : dirty
                ? "Unsaved change."
                : "Nothing to save."}
        </span>
        <div className="flex-1" />
        <Button onClick={save} disabled={!dirty || !model || saving || !probe.probe.usable}>
          {saving ? <Loader2 className="size-4 animate-spin" /> : null}
          {saving ? "Saving…" : "Save connection"}
        </Button>
      </div>
    </div>
  );
}

function Section({
  title,
  hint,
  action,
  children,
}: {
  title: string;
  hint?: string;
  action?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <section>
      <div className="mb-3 flex items-baseline gap-3">
        <h2 className="text-sm font-semibold">{title}</h2>
        {hint ? <p className="text-muted-foreground min-w-0 flex-1 text-xs">{hint}</p> : null}
        {action}
      </div>
      {children}
    </section>
  );
}
